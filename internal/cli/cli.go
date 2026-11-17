package cli

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"slices"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/fosiodev/n9ctrl/internal/core"
)

func Run(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("n9-cli", flag.ContinueOnError)
	verbose := fs.Bool("v", false, "verbose logs")
	if err := fs.Parse(args); err != nil {
		return err
	}

	lvl := slog.LevelWarn
	if *verbose {
		lvl = slog.LevelInfo
	}
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{
		Level: lvl,
	}))

	stateCh := make(chan core.State, 32)
	dev := core.New(func(s core.State) {
		select {
		case stateCh <- s:
		default:
		}
	}, logger)
	defer dev.Close()

	timeout := time.NewTimer(20 * time.Second)
	defer timeout.Stop()

	for {
		select {
		case s := <-stateCh:
			if s.Battery != nil && s.RecLoaded {
				printState(s)
				return nil
			}
		case <-timeout.C:
			st := dev.State()
			if st.Battery != nil || len(st.Rec) > 0 {
				printState(st)
				return nil
			}
			if !st.Dongle {
				return errors.New("USB dongle not found (check connection and udev rules)")
			}
			return errors.New("dongle connected, but headset did not respond (is it turned on?)")
		case <-ctx.Done():
			return ctx.Err()
		}
	}
}

func printState(s core.State) {
	rec := s.Rec
	seen := make(map[string]bool)
	mark := func(keys ...string) {
		for _, k := range keys {
			seen[k] = true
		}
	}

	w := tabwriter.NewWriter(os.Stdout, 0, 0, 3, ' ', 0)
	row := func(label, key, val string) {
		if key != "" {
			seen[key] = true
			fmt.Fprintf(w, "  %s\t[%s]\t%s\n", label, key, val)
		} else {
			fmt.Fprintf(w, "  %s\t\t%s\n", label, val)
		}
	}

	fmt.Fprintln(w, "AKG N9 Hybrid")
	fmt.Fprintln(w, strings.Repeat("─", 48))

	bat := "n/a"
	if s.Battery != nil {
		bat = fmt.Sprintf("%d%%", *s.Battery)
	}
	mode := "n/a"
	if s.Mode != nil {
		mode = *s.Mode
	}
	row("Battery", "", bat)
	row("Noise Mode", "", mode)
	row("Active Source", "source", formatSource(rec["source"]))

	fmt.Fprintln(w, "\n[Noise Control]")
	row("ANC Level", "anc_level", intStr(rec, "anc_level"))
	row("Ambient Level", "amb_level", intStr(rec, "amb_level"))
	row("Adaptive ANC", "anc_adapt", onOffStr(rec, "anc_adapt"))
	row("Auto Compensation", "auto_comp", onOffStr(rec, "auto_comp"))

	fmt.Fprintln(w, "\n[Audio & Sound]")
	row("Spatial Audio", "audio3d", pairOnType(rec, "audio3d", "type"))
	row("Bass Boost", "eq_bass", onOffStr(rec, "eq_bass"))
	row("Hi-Res Audio", "hires", onOffStr(rec, "hires"))
	row("Safe Sound", "safesound", onOffStr(rec, "safesound"))
	row("Volume Limiter", "limiter", onOffStr(rec, "limiter"))
	row("L/R Balance", "balance", pairOnType(rec, "balance", "level"))
	row("Audio Mode", "audio_mode", intStr(rec, "audio_mode"))
	row("Sound Effect", "sound_effect", intStr(rec, "sound_effect"))

	fmt.Fprintln(w, "\n[Microphone & Calls]")
	row("Mic Gain", "mic_gain", intStr(rec, "mic_gain"))
	row("Mic Effect", "mic_effect", intStr(rec, "mic_effect"))
	row("Chat Optimization", "chat_opt", onOffStr(rec, "chat_opt"))
	row("Smart Talk", "smart_talk", pairOnType(rec, "smart_talk", "latency"))

	fmt.Fprintln(w, "\n[System]")
	row("Voice Prompts", "voice_prompt", onOffStr(rec, "voice_prompt"))
	row("Auto Power Off", "auto_off", formatAutoOff(rec["auto_off"]))
	mark("auto_off")

	var extra []string
	for k := range rec {
		if !seen[k] {
			extra = append(extra, k)
		}
	}
	if len(extra) > 0 {
		slices.Sort(extra)
		fmt.Fprintln(w, "\n[Other]")
		for _, k := range extra {
			row(k, k, fmt.Sprintf("%v", rec[k]))
		}
	}

	_ = w.Flush()
}

func intStr(rec map[string]any, key string) string {
	v, ok := rec[key]
	if !ok {
		return "—"
	}
	return fmt.Sprintf("%v", v)
}

func onOffStr(rec map[string]any, key string) string {
	v, ok := rec[key]
	if !ok {
		return "—"
	}
	switch x := v.(type) {
	case bool:
		if x {
			return "on"
		}
		return "off"
	case int:
		if x != 0 {
			return "on"
		}
		return "off"
	default:
		return fmt.Sprintf("%v", v)
	}
}

func pairOnType(rec map[string]any, key, sub string) string {
	v, ok := rec[key]
	if !ok {
		return "—"
	}
	m, ok := v.(map[string]any)
	if !ok {
		return fmt.Sprintf("%v", v)
	}
	on := onOffStr(m, "on")
	if subVal, exists := m[sub]; exists && on == "on" {
		return fmt.Sprintf("on (%s=%v)", sub, subVal)
	}
	return on
}

func formatAutoOff(v any) string {
	if v == nil {
		return "—"
	}
	m, ok := v.(map[string]any)
	if !ok {
		return fmt.Sprintf("%v", v)
	}
	if on, _ := m["on"].(bool); on {
		return fmt.Sprintf("on (%v min)", m["min"])
	}
	return "off"
}

func formatSource(v any) string {
	if v == nil {
		return "—"
	}
	n, ok := v.(int)
	if !ok {
		return fmt.Sprintf("%v", v)
	}
	switch n {
	case 0:
		return "0 (Bluetooth)"
	case 1:
		return "1 (USB Dongle)"
	case 2:
		return "2 (AUX)"
	default:
		return fmt.Sprintf("%d", n)
	}
}
