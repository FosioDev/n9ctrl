// Package n9core talks to the AKG N9 Hybrid via USB dongle on Linux (/dev/hidraw*).
//
// Format:
//
//	send : HID output report 0x06 = 06 <len+1> <destination> <RACE>
//	read : HIDIOCGINPUT 0x07      = 07 <len> <destination> <len bytes> (continuous stream)
//	destination: 0x00 = dongle, 0x80 = headset
//	RACE : 05 <type> <len u16le> <id u16le> <payload>
package core

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"log/slog"
	"maps"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	"unsafe"
)

const (
	VID uint16 = 0x0ECB
	PID uint16 = 0x20D9

	TargetDongle  byte = 0x00
	TargetHeadset byte = 0x80

	VendorUsagePage uint16 = 0xFF13

	// 62, 63, 64, idk
	reportSize = 62
)

var Modes = map[string]int{
	"anc":     1,
	"ambient": 10,
	"off":     0,
}

var FilterName = map[int]string{
	1:  "anc",
	10: "ambient",
	0:  "off",
}

// State mirrors the headset & dongle state published via OnChange.
type State struct {
	Dongle    bool           `json:"dongle"`
	Headset   bool           `json:"headset"`
	Battery   *int           `json:"battery"`
	Mode      *string        `json:"mode"`
	Updated   *string        `json:"updated"`
	Rec       map[string]any `json:"rec"`
	RecLoaded bool           `json:"rec_loaded"`
}

func (s State) clone() State {
	cp := s
	if s.Battery != nil {
		v := *s.Battery
		cp.Battery = &v
	}
	if s.Mode != nil {
		v := *s.Mode
		cp.Mode = &v
	}
	if s.Updated != nil {
		v := *s.Updated
		cp.Updated = &v
	}
	cp.Rec = cloneMap(s.Rec)
	return cp
}

func cloneMap(m map[string]any) map[string]any {
	if m == nil {
		return map[string]any{}
	}
	out := maps.Clone(m)
	for k, v := range out {
		if sub, ok := v.(map[string]any); ok {
			out[k] = maps.Clone(sub)
		}
	}
	return out
}

// Race builds an Airoha RACE frame.
func Race(rid uint16, payload []byte) []byte {
	n := uint16(2 + len(payload))
	pkt := make([]byte, 6+len(payload))
	pkt[0] = 0x05
	pkt[1] = 0x5A
	binary.LittleEndian.PutUint16(pkt[2:4], n)
	binary.LittleEndian.PutUint16(pkt[4:6], rid)
	copy(pkt[6:], payload)
	return pkt
}

func hexs(b []byte) string {
	var sb strings.Builder
	for i, x := range b {
		if i > 0 {
			sb.WriteByte(' ')
		}
		fmt.Fprintf(&sb, "%02X", x)
	}
	return sb.String()
}

// ---------------------------------------------------------------- resources

type getDef struct {
	rid       uint16
	payload   []byte
	translate func(f []byte) map[string]any
}

func ltVoice(f []byte) map[string]any {
	if len(f) < 12 || f[7] != 19 {
		return nil
	}
	return map[string]any{
		"mic_effect":   int(f[9]),
		"sound_effect": int(f[10]),
		"audio_mode":   int(f[11]),
	}
}

func ltAutoOff(f []byte) map[string]any {
	if len(f) < 13 || f[7] != 1 {
		return nil
	}
	on := (uint16(f[9]) | (uint16(f[10]) << 8)) != 0
	sec := int(f[11]) | (int(f[12]) << 8)
	return map[string]any{
		"auto_off": map[string]any{
			"on":  on,
			"min": sec / 60,
		},
	}
}

func ltOne(key string) func([]byte) map[string]any {
	return func(f []byte) map[string]any {
		if len(f) < 8 || f[6] != 0 {
			return nil
		}
		return map[string]any{key: int(f[7])}
	}
}

func ltPair(key, a, b string) func([]byte) map[string]any {
	return func(f []byte) map[string]any {
		if len(f) < 9 || f[6] != 0 {
			return nil
		}
		return map[string]any{
			key: map[string]any{
				a: int(f[7]),
				b: int(f[8]),
			},
		}
	}
}

func ltVP(f []byte) map[string]any {
	if len(f) < 10 || f[6] != 1 || f[8] != 0 {
		return nil
	}
	return map[string]any{"voice_prompt": int(f[9])}
}

func ltAdapt(f []byte) map[string]any {
	if len(f) < 9 || f[6] != 0 || f[7] != 3 {
		return nil
	}
	return map[string]any{"anc_adapt": int(f[8])}
}

func ltAutoComp(f []byte) map[string]any {
	if len(f) < 15 || f[6] != 0 || f[7] != 13 {
		return nil
	}
	return map[string]any{"auto_comp": 1 - int(f[14])}
}

func ltANCLevel(f []byte) map[string]any {
	if len(f) < 12 || f[6] != 5 || f[9] != 1 {
		return nil
	}
	return map[string]any{"anc_level": int(f[10]) | (int(f[11]) << 8)}
}

func ltAMBLevel(f []byte) map[string]any {
	if len(f) < 11 || f[6] != 7 {
		return nil
	}
	return map[string]any{"amb_level": int(f[9]) | (int(f[10]) << 8)}
}

var gets = []getDef{
	{0x0E17, []byte{0x03, 0x02}, ltAdapt},
	{0x0E26, []byte{0, 13, 0, 0}, ltAutoComp},
	{0x0901, []byte{0x05, 0x00}, ltANCLevel},
	{0x0901, []byte{0x07, 0x00}, ltAMBLevel},
	{0x2C83, []byte{0x13, 0x00}, ltVoice},
	{0x2C83, []byte{0x01, 0x00}, ltAutoOff},
	{0x0013, nil, ltPair("audio3d", "on", "type")},
	{0x0014, nil, ltOne("safesound")},
	{0x0053, nil, ltOne("chat_opt")},
	{0x0051, nil, ltOne("limiter")},
	{0x0058, nil, ltOne("eq_bass")},
	{0x0060, nil, ltPair("smart_talk", "on", "latency")},
	{0x0075, nil, ltOne("hires")},
	{0x0064, nil, ltOne("mic_gain")},
	{0x0037, nil, ltPair("balance", "on", "level")},
	{0x0055, nil, ltOne("source")},
	{0x0901, []byte{0x01, 0x00}, ltVP},
}

func ok0(f []byte) bool {
	return len(f) >= 7 && f[6] == 0
}

func okVP(f []byte) bool {
	return len(f) >= 9 && f[6] == 1 && f[8] == 0
}

type setSpec struct {
	rid     uint16
	payload []byte
	check   func([]byte) bool
}

type setBuilder func(v any, current any) (setSpec, error)

func asBoolByte(v any) (byte, error) {
	switch x := v.(type) {
	case bool:
		if x {
			return 1, nil
		}
		return 0, nil
	case int:
		if x != 0 {
			return 1, nil
		}
		return 0, nil
	case float64:
		if x != 0 {
			return 1, nil
		}
		return 0, nil
	default:
		return 0, fmt.Errorf("expected bool/int, got %T", v)
	}
}

func asInt(v any) (int, error) {
	switch x := v.(type) {
	case int:
		return x, nil
	case int64:
		return int(x), nil
	case float64:
		return int(x), nil
	case bool:
		if x {
			return 1, nil
		}
		return 0, nil
	default:
		return 0, fmt.Errorf("expected number, got %T", v)
	}
}

func asMap(v any) (map[string]any, error) {
	m, ok := v.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("expected map[string]any, got %T", v)
	}
	return m, nil
}

func u16le(n int) []byte {
	b := make([]byte, 2)
	binary.LittleEndian.PutUint16(b, uint16(n))
	return b
}

var sets = map[string]setBuilder{
	"anc_adapt": func(v, _ any) (setSpec, error) {
		b, err := asBoolByte(v)
		return setSpec{0x0E17, []byte{3, b}, ok0}, err
	},
	"auto_comp": func(v, _ any) (setSpec, error) {
		b, err := asBoolByte(v)
		return setSpec{0x0E13, []byte{5, 1 - b}, ok0}, err
	},
	"anc_level": func(v, _ any) (setSpec, error) {
		n, err := asInt(v)
		return setSpec{0x0E06, append([]byte{0, 30}, u16le(n)...), ok0}, err
	},
	"amb_level": func(v, _ any) (setSpec, error) {
		n, err := asInt(v)
		return setSpec{0x0E06, append([]byte{0, 30}, u16le(n)...), ok0}, err
	},
	"balance": func(v, _ any) (setSpec, error) {
		m, err := asMap(v)
		if err != nil {
			return setSpec{}, err
		}
		on, err := asBoolByte(m["on"])
		if err != nil {
			return setSpec{}, err
		}
		lvl, err := asInt(m["level"])
		return setSpec{0x0036, []byte{on, byte(lvl)}, ok0}, err
	},
	"sound_effect": func(v, _ any) (setSpec, error) {
		n, err := asInt(v)
		return setSpec{0x2C82, []byte{19, 0, byte(n)}, ok0}, err
	},
	"mic_effect": func(v, _ any) (setSpec, error) {
		n, err := asInt(v)
		return setSpec{0x2C82, []byte{19, 0, byte(n)}, ok0}, err
	},
	"audio_mode": func(v, _ any) (setSpec, error) {
		n, err := asInt(v)
		return setSpec{0x2C82, []byte{19, 0, byte(n)}, ok0}, err
	},
	"auto_off": func(v, _ any) (setSpec, error) {
		m, err := asMap(v)
		if err != nil {
			return setSpec{}, err
		}
		on, err := asBoolByte(m["on"])
		if err != nil {
			return setSpec{}, err
		}
		mins, err := asInt(m["min"])
		return setSpec{0x2C82, append([]byte{1, 0, on, 0}, u16le(mins*60)...), ok0}, err
	},
	"audio3d": func(v, _ any) (setSpec, error) {
		m, err := asMap(v)
		if err != nil {
			return setSpec{}, err
		}
		on, err := asBoolByte(m["on"])
		if err != nil {
			return setSpec{}, err
		}
		typ, err := asInt(m["type"])
		return setSpec{0x0012, []byte{on, byte(typ)}, ok0}, err
	},
	"safesound": func(v, _ any) (setSpec, error) {
		b, err := asBoolByte(v)
		return setSpec{0x0015, []byte{b}, ok0}, err
	},
	"chat_opt": func(v, _ any) (setSpec, error) {
		b, err := asBoolByte(v)
		return setSpec{0x0052, []byte{b}, ok0}, err
	},
	"limiter": func(v, _ any) (setSpec, error) {
		b, err := asBoolByte(v)
		return setSpec{0x0050, []byte{b}, ok0}, err
	},
	"eq_bass": func(v, _ any) (setSpec, error) {
		b, err := asBoolByte(v)
		return setSpec{0x0059, []byte{b}, ok0}, err
	},
	"smart_talk": func(v, _ any) (setSpec, error) {
		m, err := asMap(v)
		if err != nil {
			return setSpec{}, err
		}
		on, err := asBoolByte(m["on"])
		if err != nil {
			return setSpec{}, err
		}
		lat, err := asInt(m["latency"])
		return setSpec{0x0061, []byte{on, byte(lat)}, ok0}, err
	},
	"source": func(v, _ any) (setSpec, error) {
		n, err := asInt(v)
		return setSpec{0x0054, []byte{byte(n)}, ok0}, err
	},
	"hires": func(v, _ any) (setSpec, error) {
		b, err := asBoolByte(v)
		return setSpec{0x0076, []byte{b}, ok0}, err
	},
	"voice_prompt": func(v, _ any) (setSpec, error) {
		b, err := asBoolByte(v)
		return setSpec{0x0900, []byte{1, 0, b}, okVP}, err
	},
}

// ---------------------------------------------------------------- N9 controller

type listener struct {
	id     uint64
	rid    uint16
	hasTyp bool
	typ    byte
	ch     chan []byte
}

type N9 struct {
	onChange func(State)
	log      *slog.Logger

	ctx    context.Context
	cancel context.CancelFunc
	wg     sync.WaitGroup

	ioMu sync.Mutex
	fd   int // -1 when disconnected

	cmdMu sync.Mutex

	stateMu sync.Mutex
	state   State

	bufs map[byte][]byte

	listMu     sync.Mutex
	nextListID uint64
	listeners  map[uint64]*listener

	pendMu  sync.Mutex
	pending map[uint16]int

	winMu      sync.Mutex
	windowOpen bool

	wakeCh chan struct{}

	timerMu  sync.Mutex
	recTimer *time.Timer
}

func New(onChange func(State), logger *slog.Logger) *N9 {
	if logger == nil {
		logger = slog.Default()
	}
	ctx, cancel := context.WithCancel(context.Background())
	n := &N9{
		onChange:  onChange,
		log:       logger,
		ctx:       ctx,
		cancel:    cancel,
		fd:        -1,
		state:     State{Rec: map[string]any{}},
		bufs:      make(map[byte][]byte),
		listeners: make(map[uint64]*listener),
		pending:   make(map[uint16]int),
		wakeCh:    make(chan struct{}, 1),
	}
	n.wg.Add(2)
	go n.reader()
	go n.watcher()
	return n
}

func (n *N9) State() State {
	n.stateMu.Lock()
	defer n.stateMu.Unlock()
	return n.state.clone()
}

func (n *N9) SetWindowOpen(open bool) {
	n.winMu.Lock()
	n.windowOpen = open
	n.winMu.Unlock()
	if open {
		n.Wake()
	}
}

func (n *N9) Wake() {
	select {
	case n.wakeCh <- struct{}{}:
	default:
	}
}

func (n *N9) Close() {
	n.cancel()
	n.timerMu.Lock()
	if n.recTimer != nil {
		n.recTimer.Stop()
	}
	n.timerMu.Unlock()
	n.closeDev()
	n.wg.Wait()
}

func (n *N9) isWindowOpen() bool {
	n.winMu.Lock()
	defer n.winMu.Unlock()
	return n.windowOpen
}

func stateEqual(a, b State) bool {
	return a.Dongle == b.Dongle &&
		a.Headset == b.Headset &&
		a.RecLoaded == b.RecLoaded &&
		ptrEqual(a.Battery, b.Battery) &&
		ptrEqual(a.Mode, b.Mode) &&
		ptrEqual(a.Updated, b.Updated) &&
		recEqual(a.Rec, b.Rec)
}

func ptrEqual[T comparable](a, b *T) bool {
	if a == nil || b == nil {
		return a == b
	}
	return *a == *b
}

func recEqual(a, b map[string]any) bool {
	if len(a) != len(b) {
		return false
	}
	for k, va := range a {
		vb, ok := b[k]
		if !ok {
			return false
		}
		if ma, ok1 := va.(map[string]any); ok1 {
			mb, ok2 := vb.(map[string]any)
			if !ok2 || !recEqual(ma, mb) {
				return false
			}
			continue
		}
		if va != vb {
			return false
		}
	}
	return true
}

func (n *N9) publish(fn func(*State)) {
	n.stateMu.Lock()
	next := n.state.clone()
	fn(&next)
	changed := !stateEqual(next, n.state)
	if changed {
		n.state = next
	}
	snapshot := next.clone()
	n.stateMu.Unlock()

	if changed && n.onChange != nil {
		n.onChange(snapshot)
	}
}

func (n *N9) hasDev() bool {
	n.ioMu.Lock()
	defer n.ioMu.Unlock()
	return n.fd >= 0
}

func (n *N9) closeDev() {
	n.ioMu.Lock()
	defer n.ioMu.Unlock()
	if n.fd >= 0 {
		_ = syscall.Close(n.fd)
		n.fd = -1
	}
	clear(n.bufs)
}

// ---------------------------------------------------------------- Linux hidraw discovery & IO

// findAndOpen searches /sys/class/hidraw/hidraw* for VID/PID and Usage Page 0xFF13.
func findAndOpen() (int, string, error) {
	entries, err := filepath.Glob("/sys/class/hidraw/hidraw*")
	if err != nil {
		return -1, "", err
	}
	var lastErr error
	for _, sysDir := range entries {
		uevent, err := os.ReadFile(filepath.Join(sysDir, "device", "uevent"))
		if err != nil {
			continue
		}
		if !matchHIDID(string(uevent), VID, PID) {
			continue
		}
		rdesc, err := os.ReadFile(filepath.Join(sysDir, "device", "report_descriptor"))
		if err != nil || !hasUsagePage(rdesc, VendorUsagePage) {
			continue
		}
		devPath := "/dev/" + filepath.Base(sysDir)
		fd, err := syscall.Open(devPath, syscall.O_RDWR|syscall.O_CLOEXEC, 0)
		if err != nil {
			lastErr = fmt.Errorf("open %s: %w", devPath, err)
			continue
		}
		return fd, devPath, nil
	}
	if lastErr != nil {
		return -1, "", lastErr
	}
	return -1, "", os.ErrNotExist
}

func matchHIDID(uevent string, vid, pid uint16) bool {
	for line := range strings.SplitSeq(uevent, "\n") {
		if !strings.HasPrefix(line, "HID_ID=") {
			continue
		}
		parts := strings.Split(strings.TrimPrefix(line, "HID_ID="), ":")
		if len(parts) != 3 {
			continue
		}
		v, err1 := strconv.ParseUint(parts[1], 16, 32)
		p, err2 := strconv.ParseUint(parts[2], 16, 32)
		if err1 == nil && err2 == nil && uint16(v) == vid && uint16(p) == pid {
			return true
		}
	}
	return false
}

func hasUsagePage(desc []byte, target uint16) bool {
	for i := 0; i < len(desc); {
		b := desc[i]
		if b == 0xFE { // Long item
			if i+1 >= len(desc) {
				break
			}
			i += 3 + int(desc[i+1])
			continue
		}
		sizeCode := b & 0x03
		size := int(sizeCode)
		if sizeCode == 3 {
			size = 4
		}
		if i+1+size > len(desc) {
			break
		}
		if (b & 0xFC) == 0x04 { // Global Usage Page item
			var page uint32
			for j := 0; j < size; j++ {
				page |= uint32(desc[i+1+j]) << (8 * j)
			}
			if page == uint32(target) {
				return true
			}
		}
		i += 1 + size
	}
	return false
}

// _IOC(_IOC_WRITE|_IOC_READ, 'H', 0x0A, length) for Linux asm-generic ioctl (amd64, arm64, 386, arm, riscv64).
func hidIOCGInput(length int) uintptr {
	const (
		iocNRShift   = 0
		iocTypeShift = 8
		iocSizeShift = 16
		iocDirShift  = 30
		iocRW        = 1 | 2
	)
	return uintptr((iocRW << iocDirShift) | (length << iocSizeShift) | ('H' << iocTypeShift) | (0x0A << iocNRShift))
}

func (n *N9) getInputReport(reportID byte, length int) ([]byte, error) {
	buf := make([]byte, length)
	buf[0] = reportID

	n.ioMu.Lock()
	defer n.ioMu.Unlock()
	if n.fd < 0 {
		return nil, errors.New("no dongle")
	}

	r1, _, errno := syscall.Syscall(
		syscall.SYS_IOCTL,
		uintptr(n.fd),
		hidIOCGInput(len(buf)),
		uintptr(unsafe.Pointer(&buf[0])),
	)
	if errno != 0 {
		return nil, errno
	}
	got := min(int(r1), len(buf))
	return buf[:got], nil
}

func (n *N9) send(target byte, pkt []byte) error {
	rep := make([]byte, reportSize)
	rep[0] = 0x06
	rep[1] = byte(len(pkt) + 1)
	rep[2] = target
	copy(rep[3:], pkt)

	n.ioMu.Lock()
	defer n.ioMu.Unlock()
	if n.fd < 0 {
		return errors.New("no dongle")
	}
	_, err := syscall.Write(n.fd, rep)
	return err
}

// ---------------------------------------------------------------- reader & frame parsing

func (n *N9) reader() {
	defer n.wg.Done()
	for n.ctx.Err() == nil {
		if !n.hasDev() {
			if !sleepCtx(n.ctx, 200*time.Millisecond) {
				return
			}
			continue
		}
		r, err := n.getInputReport(0x07, reportSize)
		if err != nil {
			n.log.Warn("dongle disappeared", "err", err)
			n.closeDev()
			n.publish(func(s *State) {
				s.Dongle = false
				s.Headset = false
				s.RecLoaded = false
			})
			continue
		}
		if len(r) >= 3 && r[1] > 0 {
			target := r[2]
			end := min(3+int(r[1]), len(r))
			n.bufs[target] = n.split(target, append(n.bufs[target], r[3:end]...))
		} else {
			if !sleepCtx(n.ctx, 30*time.Millisecond) {
				return
			}
		}
	}
}

func (n *N9) split(target byte, buf []byte) []byte {
	for {
		i := bytes.IndexByte(buf, 0x05)
		if i < 0 {
			return nil
		}
		if len(buf)-i < 6 {
			return append([]byte(nil), buf[i:]...)
		}
		typ := buf[i+1]
		if typ != 0x5A && typ != 0x5B && typ != 0x5C && typ != 0x5D {
			buf = buf[i+1:]
			continue
		}
		length := int(buf[i+2]) | (int(buf[i+3]) << 8)
		if length < 2 || length > 600 {
			buf = buf[i+1:]
			continue
		}
		if len(buf)-i < 4+length {
			return append([]byte(nil), buf[i:]...)
		}
		frame := append([]byte(nil), buf[i:i+4+length]...)
		buf = buf[i+4+length:]
		if frame[4] == 0x92 && frame[5] == 0x0F { // firmware debug log
			continue
		}
		n.onFrame(target, frame)
	}
}

func (n *N9) onFrame(target byte, f []byte) {
	rid := uint16(f[4]) | (uint16(f[5]) << 8)

	n.listMu.Lock()
	for _, l := range n.listeners {
		if target == TargetHeadset && l.rid == rid && (!l.hasTyp || l.typ == f[1]) {
			select {
			case l.ch <- f:
			default:
			}
		}
	}
	n.listMu.Unlock()

	if target == TargetHeadset && rid == 0x0901 && f[1] == 0x5D && len(f) >= 10 {
		n.log.Info("noise notification 0x0901", "data", hexs(f[6:]))
		mode, ok := FilterName[int(f[9])]
		if !ok {
			mode = fmt.Sprintf("filter %d", f[9])
		}
		n.publish(func(s *State) {
			s.Headset = true
			s.Mode = &mode
		})
	}

	if target == TargetHeadset && (f[1] == 0x5C || f[1] == 0x5D) && rid != 0x0CD6 {
		n.pendMu.Lock()
		isPending := n.pending[rid] > 0
		n.pendMu.Unlock()

		if rid != 0x0901 && !isPending {
			n.log.Info("headset notification", "rid", fmt.Sprintf("0x%04X", rid), "type", fmt.Sprintf("%02X", f[1]), "data", hexs(f[6:]))
		}
		for _, g := range gets {
			if g.rid == rid {
				if updates := g.translate(f); len(updates) > 0 {
					n.publish(func(s *State) {
						maps.Copy(s.Rec, updates)
					})
				}
			}
		}
		if !isPending {
			n.reloadSoon(1200 * time.Millisecond)
		}
	}
}

func (n *N9) reloadSoon(delay time.Duration) {
	n.timerMu.Lock()
	defer n.timerMu.Unlock()
	if n.recTimer != nil {
		n.recTimer.Stop()
	}
	n.recTimer = time.AfterFunc(delay, func() {
		if n.ctx.Err() != nil || !n.hasDev() {
			return
		}
		if n.State().Headset {
			n.ReadResources()
		}
	})
}

// ---------------------------------------------------------------- requests

func (n *N9) Request(pkt []byte, rid uint16, typ *byte, timeout time.Duration, attempts int) []byte {
	n.cmdMu.Lock()
	defer n.cmdMu.Unlock()

	for range attempts {
		if n.ctx.Err() != nil {
			return nil
		}
		ch := make(chan []byte, 1)

		n.listMu.Lock()
		n.nextListID++
		lid := n.nextListID
		l := &listener{id: lid, rid: rid, ch: ch}
		if typ != nil {
			l.hasTyp = true
			l.typ = *typ
		}
		n.listeners[lid] = l
		n.listMu.Unlock()

		n.pendMu.Lock()
		n.pending[rid]++
		n.pendMu.Unlock()

		err := n.send(TargetHeadset, pkt)
		var resp []byte
		if err == nil {
			timer := time.NewTimer(timeout)
			select {
			case resp = <-ch:
			case <-timer.C:
			case <-n.ctx.Done():
			}
			timer.Stop()
		}

		n.listMu.Lock()
		delete(n.listeners, lid)
		n.listMu.Unlock()

		sleepCtx(n.ctx, 50*time.Millisecond)
		n.pendMu.Lock()
		n.pending[rid]--
		if n.pending[rid] <= 0 {
			delete(n.pending, rid)
		}
		n.pendMu.Unlock()

		if err != nil {
			return nil
		}
		if resp != nil {
			return resp
		}
	}
	return nil
}

func (n *N9) ReadBattery() *int {
	typ := byte(0x5D)
	f := n.Request(Race(0x0CD6, []byte{0x00}), 0x0CD6, &typ, 2500*time.Millisecond, 2)
	if len(f) >= 9 {
		b := int(f[8])
		return &b
	}
	return nil
}

func (n *N9) ReadMode() *string {
	f := n.Request(Race(0x0901, []byte{0x05, 0x00}), 0x0901, nil, 2500*time.Millisecond, 2)
	if len(f) >= 10 {
		mode, ok := FilterName[int(f[9])]
		if !ok {
			mode = fmt.Sprintf("filter %d", f[9])
		}
		return &mode
	}
	return nil
}

func (n *N9) SetMode(mode string) (bool, error) {
	var pkt []byte
	switch mode {
	case "off":
		pkt = Race(0x0E06, []byte{0x00, 0x0B})
	case "anc":
		pkt = Race(0x0E06, []byte{0, 0x0A, 1, 0})
	case "ambient":
		pkt = Race(0x0E06, []byte{0, 0x0A, 10, 4})
	default:
		return false, fmt.Errorf("unknown mode %q", mode)
	}
	f := n.Request(pkt, 0x0E06, nil, 2500*time.Millisecond, 2)
	ok := len(f) >= 7 && f[6] == 0
	n.log.Info("set_mode", "mode", mode, "resp", hexsOrNone(f))
	if ok {
		m := mode
		n.publish(func(s *State) {
			s.Headset = true
			s.Mode = &m
		})
	}
	time.AfterFunc(800*time.Millisecond, func() {
		if n.ctx.Err() == nil {
			n.Update(false)
		}
	})
	return ok, nil
}

func (n *N9) ReadResources() {
	updates := make(map[string]any)
	for _, g := range gets {
		if n.ctx.Err() != nil || !n.hasDev() {
			break
		}
		f := n.Request(Race(g.rid, g.payload), g.rid, nil, 1500*time.Millisecond, 2)
		if f == nil {
			continue
		}
		if res := g.translate(f); len(res) > 0 {
			maps.Copy(updates, res)
		}
	}
	if n.ctx.Err() == nil && n.hasDev() {
		n.publish(func(s *State) {
			maps.Copy(s.Rec, updates)
			s.RecLoaded = true
		})
	}
}

func (n *N9) SetResource(key string, value any) (bool, error) {
	build, ok := sets[key]
	if !ok || !n.hasDev() {
		return false, nil
	}
	cur := n.State().Rec[key]
	if curMap, ok1 := cur.(map[string]any); ok1 {
		if valMap, ok2 := value.(map[string]any); ok2 {
			merged := maps.Clone(curMap)
			maps.Copy(merged, valMap)
			value = merged
		}
	}

	spec, err := build(value, cur)
	if err != nil {
		return false, err
	}
	f := n.Request(Race(spec.rid, spec.payload), spec.rid, nil, 2500*time.Millisecond, 1)
	ok = f != nil && spec.check(f)
	n.log.Info("set_resource", "key", key, "val", value, "resp", hexsOrNone(f))

	if key == "source" {
		if ok {
			src, _ := asInt(value)
			n.publish(func(s *State) {
				s.Rec["source"] = src
			})
		}
		return ok, nil
	}

	n.ReadResources()
	newVal := n.State().Rec[key]
	if ok && newVal != nil {
		ok = matchResourceValue(key, value, newVal)
	}
	return ok, nil
}

func matchResourceValue(key string, want, got any) bool {
	if wantMap, ok1 := want.(map[string]any); ok1 {
		gotMap, ok2 := got.(map[string]any)
		if !ok2 {
			return false
		}
		for k, wv := range wantMap {
			if k == "on" {
				wb, err1 := asBoolByte(wv)
				gb, err2 := asBoolByte(gotMap[k])
				if err1 != nil || err2 != nil || wb != gb {
					return false
				}
			} else {
				wi, err1 := asInt(wv)
				gi, err2 := asInt(gotMap[k])
				if err1 != nil || err2 != nil || wi != gi {
					return false
				}
			}
		}
		return true
	}

	switch key {
	case "safesound", "chat_opt", "limiter", "eq_bass", "hires", "voice_prompt", "anc_adapt", "auto_comp":
		wb, err1 := asBoolByte(want)
		gb, err2 := asBoolByte(got)
		return err1 == nil && err2 == nil && wb == gb
	default:
		wi, err1 := asInt(want)
		gi, err2 := asInt(got)
		return err1 == nil && err2 == nil && wi == gi
	}
}

// ---------------------------------------------------------------- loop

func (n *N9) Update(battery bool) {
	if !n.hasDev() {
		return
	}
	mode := n.ReadMode()
	var bat *int
	if battery || n.State().Battery == nil {
		bat = n.ReadBattery()
	}
	n.publish(func(s *State) {
		s.Headset = mode != nil
		if mode != nil {
			s.Mode = mode
			now := time.Now().Format("15:04")
			s.Updated = &now
		}
		if bat != nil {
			s.Battery = bat
		}
	})
}

func (n *N9) watcher() {
	defer n.wg.Done()
	var lastBat, lastRec time.Time

	for n.ctx.Err() == nil {
		if !n.hasDev() {
			fd, path, err := findAndOpen()
			if err == nil {
				n.ioMu.Lock()
				n.fd = fd
				n.ioMu.Unlock()
				n.log.Info("dongle connected", "path", path)
				n.publish(func(s *State) { s.Dongle = true })
				if !sleepCtx(n.ctx, 800*time.Millisecond) {
					return
				}
				lastBat = time.Time{}
			} else {
				if !errors.Is(err, os.ErrNotExist) {
					n.log.Warn("dongle open failed (check udev permissions)", "err", err)
				}
				n.publish(func(s *State) {
					s.Dongle = false
					s.Headset = false
					s.RecLoaded = false
				})
				if !sleepCtx(n.ctx, 3*time.Second) {
					return
				}
				continue
			}
		}

		now := time.Now()
		needBat := lastBat.IsZero() || now.Sub(lastBat) > 120*time.Second
		n.Update(needBat)
		if needBat {
			lastBat = now
		}

		st := n.State()
		if st.Headset && (len(st.Rec) == 0 || (n.isWindowOpen() && now.Sub(lastRec) > 8*time.Second)) {
			n.ReadResources()
			lastRec = now
		}

		wait := 20 * time.Second
		if n.isWindowOpen() {
			wait = 4 * time.Second
		}
		timer := time.NewTimer(wait)
		select {
		case <-n.wakeCh:
		case <-timer.C:
		case <-n.ctx.Done():
			timer.Stop()
			return
		}
		timer.Stop()
	}
}

func sleepCtx(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
		return true
	case <-ctx.Done():
		return false
	}
}

func hexsOrNone(b []byte) string {
	if len(b) == 0 {
		return "no response"
	}
	return hexs(b)
}
