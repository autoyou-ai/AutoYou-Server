#!/usr/bin/env bash
# Capture the Social surfaces on both clients, headlessly.
#
# The paid Social screens — the shared-computers dashboard, the room lobby,
# contacts — sit behind a sign-in, and both platforms refuse a fabricated
# session by design. So this does not try to create one: it drives whatever
# session the device already has, and says plainly when there is none.
#
# What it removes is the part that was genuinely manual. iOS always shows an
# "Open in AutoYou?" confirmation for a custom-scheme open, so a deep link
# cannot land a harness on a tab without a tap; both apps carry a debug-only
# launch hook instead, and this script uses it. One run captures every surface
# on both platforms rather than someone tapping through twice.
#
#   scripts/social-verify/run.sh [outdir]
set -uo pipefail

OUT="${1:-$(mktemp -d)}"
mkdir -p "$OUT"
ADB="${ANDROID_HOME:-$HOME/Library/Android/sdk}/platform-tools/adb"
IOS_UDID="${AUTOYOU_SIM_UDID:-}"
TABS="connection social chat"
status=0

note() { printf '  %s\n' "$*"; }

# ── iOS ─────────────────────────────────────────────────────────────────
if command -v xcrun >/dev/null 2>&1; then
    if [ -z "$IOS_UDID" ]; then
        IOS_UDID=$(xcrun simctl list devices booted 2>/dev/null \
            | sed -n 's/.*(\([0-9A-F-]\{36\}\)) (Booted).*/\1/p' | head -1)
    fi
    if [ -n "$IOS_UDID" ]; then
        echo "iOS $IOS_UDID"

        # Capture whatever is on screen before touching anything.
        #
        # A simulator build is signed "to run locally" and carries empty
        # entitlements, so it has no application-identifier and the Keychain
        # refuses to store the session (-34018). A signed-in session therefore
        # exists only in memory, and relaunching the app to select a tab throws
        # it away — which is how this harness kept reporting a paywall for an
        # account that had just signed in. So: if a session is live, work with
        # the screen as found rather than restarting into it.
        for _ in $(seq 1 6); do
            xcrun simctl io "$IOS_UDID" screenshot "$OUT/ios-current.png" >/dev/null 2>&1
        done
        note "captured ios-current.png (screen as found)"

        if [ "${AUTOYOU_VERIFY_RELAUNCH_IOS:-0}" = "1" ]; then
            for tab in $TABS; do
                xcrun simctl terminate "$IOS_UDID" com.autoyou.app >/dev/null 2>&1
                SIMCTL_CHILD_AUTOYOU_UITEST_TAB="$tab" \
                    xcrun simctl launch "$IOS_UDID" com.autoyou.app >/dev/null 2>&1
                # The splash animates; take a run of shots and keep the last,
                # which is steadier than guessing at a sleep.
                for _ in $(seq 1 25); do
                    xcrun simctl io "$IOS_UDID" screenshot "$OUT/ios-$tab.png" >/dev/null 2>&1
                done
                if [ -s "$OUT/ios-$tab.png" ]; then note "captured ios-$tab.png"
                else note "FAILED ios-$tab"; status=1; fi
            done
        else
            note "not relaunching (a live session would not survive it)"
            note "set AUTOYOU_VERIFY_RELAUNCH_IOS=1 to capture each tab from cold"
        fi
    else
        note "no booted simulator; skipping iOS"
    fi
fi

# ── Android ─────────────────────────────────────────────────────────────
if [ -x "$ADB" ] && [ -n "$("$ADB" devices | awk 'NR>1 && $2=="device"')" ]; then
    echo "Android"
    # Android is tapped rather than launched into a tab: adb can synthesise
    # touch, so the tab bar is reachable directly and there is no need for a
    # launch hook the app has to honour. iOS gets the hook because it has no
    # equivalent — every custom-scheme open there raises a confirmation.
    "$ADB" shell am force-stop com.autoyou.app >/dev/null 2>&1
    "$ADB" shell am start -n com.autoyou.app/.MainActivity >/dev/null 2>&1
    for _ in $(seq 1 15); do "$ADB" exec-out screencap -p >/dev/null 2>&1; done

    # Tab-bar centres as a fraction of width, read off the five-tab bar.
    for tab in $TABS; do
        case "$tab" in
            connection) frac=10 ;;
            social)     frac=30 ;;
            chat)       frac=50 ;;
            browser)    frac=70 ;;
            settings)   frac=90 ;;
            *)          frac=10 ;;
        esac
        size=$("$ADB" shell wm size 2>/dev/null | sed -n 's/.*: \([0-9]*\)x\([0-9]*\).*/\1 \2/p')
        w=$(echo "$size" | awk '{print $1}'); h=$(echo "$size" | awk '{print $2}')
        [ -n "$w" ] || { w=1080; h=2400; }
        # A stray tap can open a modal — the premium sheet, a dialog — and every
        # tap after it lands on that instead of the tab bar. Back out first so
        # each tab starts from the same place.
        "$ADB" shell input keyevent KEYCODE_BACK >/dev/null 2>&1
        "$ADB" shell input tap $(( w * frac / 100 )) $(( h * 93 / 100 )) >/dev/null 2>&1
        for _ in $(seq 1 8); do
            "$ADB" exec-out screencap -p > "$OUT/android-$tab.png" 2>/dev/null
        done
        # The text dump is what makes a failure readable in CI, where nobody
        # is going to open the PNG.
        "$ADB" shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1
        "$ADB" shell cat /sdcard/ui.xml 2>/dev/null \
            | grep -oE 'text="[^"]{3,80}"' > "$OUT/android-$tab.txt" 2>/dev/null
        # Assert we actually landed where we meant to. Capturing the wrong
        # screen and calling it a pass is the failure mode that matters here:
        # a run that never reached Social cannot tell you anything about Social,
        # but it will happily report success if nobody checks.
        case "$tab" in
            connection) marker="Connection Status" ;;
            social)     marker="Social" ;;
            chat)       marker="Chat" ;;
            browser)    marker="Browser" ;;
            settings)   marker="Settings" ;;
            *)          marker="" ;;
        esac
        if [ ! -s "$OUT/android-$tab.png" ]; then
            note "FAILED android-$tab (no capture)"; status=1
        elif [ -n "$marker" ] && ! grep -qi "$marker" "$OUT/android-$tab.txt" 2>/dev/null; then
            note "FAILED android-$tab (landed elsewhere; \"$marker\" not on screen)"; status=1
        else
            note "captured android-$tab.png"
        fi
    done
else
    note "no attached emulator; skipping Android"
fi

# ── Did we actually get past the gate? ───────────────────────────────────
echo
verdict="ok"
if [ "$status" -ne 0 ]; then
    verdict="capture-failed"
    echo "CAPTURE FAILED — at least one screen was not reached, so this run"
    echo "cannot be read as a pass. See the notes above."
elif ! grep -qi "Social" "$OUT"/android-social.txt 2>/dev/null; then
    verdict="capture-failed"
    status=1
    echo "CAPTURE FAILED — never reached Android's Social tab."
elif grep -qi "Paid AutoYou Cloud account required" "$OUT"/android-social.txt 2>/dev/null; then
    # Per platform, because the two devices are deliberately signed in to
    # different accounts for the cross-account pairing cases; one paywall does
    # not make the whole run signed-out.
    echo "ANDROID SIGNED OUT — Social is showing the paywall there, so the"
    echo "dashboard and lobby are not in the android captures."
    verdict="android-signed-out"
    status=2
fi

# The verdict is also written down, because an exit code does not survive a
# pipe — `run.sh | tail` reports tail's status, which is how a failing deploy
# looked green earlier in this project's history. A caller that pipes for
# readability can still read the real answer out of this file.
printf '%s\n' "$verdict" > "$OUT/result.txt"

echo "verdict: $verdict"
echo "captures in $OUT"
exit $status
