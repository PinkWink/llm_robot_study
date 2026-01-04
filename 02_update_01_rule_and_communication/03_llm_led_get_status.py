#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Ollama + Arduino Serial LED(13) controller with conditional commands.
Upgraded protocol with sequence numbers for 1:1 request-response matching.

Arduino protocol:
  PC -> Arduino:  ON <seq> | OFF <seq> | STATUS <seq>
  Arduino -> PC:  STATE <seq> ON|OFF
                 ERR <seq> ...
                 READY
"""

import json
import time
import re
import requests
import serial
from dataclasses import dataclass

# =========================
# Config
# =========================
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "phi3:mini"  # or qwen3:0.6b etc.

SERIAL_PORT = "/dev/tty.usbmodem21101"  # mac example
BAUD = 115200
TIMEOUT_S = 0.2  # readline timeout (small; we loop)

ENABLE_RULE_FALLBACK = True

SYSTEM_PROMPT = """너는 아두이노 LED(13번 핀) 제어를 위한 의도 파서다.
사용자 문장을 보고 반드시 아래 JSON 하나만 출력한다(설명/문장/코드블록 금지).

스키마:
{
  "intent": "turn_on" | "turn_off" | "get_status" | "toggle" | "conditional" | "unknown",
  "condition": "is_on" | "is_off" | null,
  "action": "turn_on" | "turn_off" | "toggle" | null,
  "reply": "사용자에게 보여줄 한글 한 문장"
}

규칙:
- LED 켜기면 intent=turn_on
- LED 끄기면 intent=turn_off
- LED 상태 질문이면 intent=get_status
- 토글(반전) 의미면 intent=toggle
- 조건부 문장(예: '켜져 있으면 꺼줘', '꺼져 있으면 켜줘')이면:
  intent=conditional, condition은 is_on/is_off, action은 turn_on/turn_off/toggle 중 하나
- 애매하면 intent=unknown
- condition/action은 conditional에서만 채우고, 그 외에는 null
"""


# =========================
# Ollama call
# =========================
def ollama_parse_intent(user_text: str) -> dict:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.0},
    }
    r = requests.post(OLLAMA_URL, json=payload, timeout=30)
    r.raise_for_status()
    content = r.json()["message"]["content"]
    return json.loads(content)


# =========================
# Rule fallback
# =========================
def rule_fallback_parse(user_text: str) -> dict:
    t = user_text.strip()
    t_compact = re.sub(r"\s+", " ", t)

    cond_on = re.search(r"(켜져|켜져있|켜져 있)\s*으면", t_compact)
    cond_off = re.search(r"(꺼져|꺼져있|꺼져 있)\s*으면", t_compact)

    want_on = re.search(r"(켜줘|켜라|켜|불\s*켜|on|ON)\b", t_compact)
    want_off = re.search(r"(꺼줘|꺼라|꺼|불\s*꺼|off|OFF)\b", t_compact)
    want_toggle = re.search(r"(토글|반전|바꿔|스위치|toggle)", t_compact, re.IGNORECASE)

    want_status = re.search(r"(상태|켜져\s*있|꺼져\s*있|불\s*켜졌|불\s*꺼졌|켜져\?|꺼져\?)", t_compact)

    if cond_on or cond_off:
        condition = "is_on" if cond_on else "is_off"
        if want_toggle:
            action = "toggle"
        elif want_on and not want_off:
            action = "turn_on"
        elif want_off and not want_on:
            action = "turn_off"
        else:
            action = None
        return {"intent": "conditional", "condition": condition, "action": action, "reply": "조건을 확인한 뒤 동작할게요."}

    if want_status:
        return {"intent": "get_status", "condition": None, "action": None, "reply": "LED 상태를 확인할게요."}
    if want_toggle:
        return {"intent": "toggle", "condition": None, "action": None, "reply": "LED를 반전(토글)할게요."}
    if want_on and not want_off:
        return {"intent": "turn_on", "condition": None, "action": None, "reply": "LED를 켤게요."}
    if want_off and not want_on:
        return {"intent": "turn_off", "condition": None, "action": None, "reply": "LED를 끌게요."}

    return {"intent": "unknown", "condition": None, "action": None, "reply": "무슨 뜻인지 애매해요."}


# =========================
# Serial protocol
# =========================
@dataclass
class ArduinoStateResp:
    seq: int
    is_on: bool


class ArduinoClient:
    def __init__(self, port: str, baud: int = 115200, timeout_s: float = 0.2):
        self.ser = serial.Serial(port, baud, timeout=timeout_s)
        time.sleep(2.0)  # allow Arduino reset
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        self._seq = 1

        # absorb any boot lines like READY
        self._drain(1.0)

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def _next_seq(self) -> int:
        s = self._seq
        self._seq += 1
        if self._seq > 2_000_000_000:
            self._seq = 1
        return s

    def _drain(self, seconds: float):
        t_end = time.time() + seconds
        while time.time() < t_end:
            line = self.ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            # print("[DRAIN]", line)

    def _readline(self) -> str | None:
        line = self.ser.readline().decode("utf-8", errors="ignore").strip()
        return line if line else None

    def _wait_for_seq_state(self, seq: int, timeout_s: float = 2.0) -> ArduinoStateResp:
        """
        Wait until we receive:
          STATE <seq> ON|OFF
        Ignore READY, other STATEs, etc.
        """
        t_end = time.time() + timeout_s
        while time.time() < t_end:
            line = self._readline()
            if not line:
                continue

            # print("[RX]", line)

            if line == "READY":
                continue

            if line.startswith("STATE "):
                # Expected: STATE <seq> ON|OFF
                parts = line.split()
                if len(parts) == 3:
                    try:
                        rx_seq = int(parts[1])
                    except ValueError:
                        continue
                    if rx_seq != seq:
                        continue
                    val = parts[2].upper()
                    if val == "ON":
                        return ArduinoStateResp(seq=seq, is_on=True)
                    if val == "OFF":
                        return ArduinoStateResp(seq=seq, is_on=False)

            if line.startswith("ERR "):
                # ERR <seq> CODE ...
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        rx_seq = int(parts[1])
                    except ValueError:
                        rx_seq = None
                    if rx_seq == seq:
                        raise RuntimeError(f"Arduino error for seq={seq}: {line}")

        raise TimeoutError(f"Timeout waiting for STATE seq={seq}")

    def cmd(self, verb: str) -> ArduinoStateResp:
        seq = self._next_seq()
        msg = f"{verb} {seq}\n"
        self.ser.write(msg.encode("utf-8"))
        self.ser.flush()
        return self._wait_for_seq_state(seq, timeout_s=2.0)

    def status(self) -> bool | None:
        try:
            return self.cmd("STATUS").is_on
        except Exception:
            return None

    def on(self) -> bool | None:
        try:
            return self.cmd("ON").is_on
        except Exception:
            return None

    def off(self) -> bool | None:
        try:
            return self.cmd("OFF").is_on
        except Exception:
            return None


# =========================
# High-level actions
# =========================
def toggle_led(dev: ArduinoClient) -> bool | None:
    st = dev.status()
    if st is None:
        return None
    return dev.off() if st else dev.on()


def handle_intent(dev: ArduinoClient, intent_obj: dict) -> str:
    intent = intent_obj.get("intent", "unknown")
    condition = intent_obj.get("condition", None)
    action = intent_obj.get("action", None)
    reply = intent_obj.get("reply", "")

    if intent == "turn_on":
        st = dev.on()
        return "LED 켰어요." if st is True else "LED 켜기를 시도했는데 실패했어요."

    if intent == "turn_off":
        st = dev.off()
        return "LED 껐어요." if st is False else "LED 끄기를 시도했는데 실패했어요."

    if intent == "get_status":
        st = dev.status()
        if st is True:
            return "지금 LED는 켜져 있어요."
        if st is False:
            return "지금 LED는 꺼져 있어요."
        return "상태를 읽지 못했어요."

    if intent == "toggle":
        st = toggle_led(dev)
        if st is True:
            return "토글했어요. 지금은 켜짐."
        if st is False:
            return "토글했어요. 지금은 꺼짐."
        return "토글에 실패했어요. 상태를 읽지 못했어요."

    if intent == "conditional":
        st = dev.status()
        if st is None:
            return "조건을 확인하려고 했는데 LED 상태를 읽지 못했어요."

        cond_ok = (condition == "is_on" and st is True) or (condition == "is_off" and st is False)
        if not cond_ok:
            if condition == "is_on":
                return "지금 LED가 켜져 있지 않아서 아무 것도 하지 않았어요."
            if condition == "is_off":
                return "지금 LED가 꺼져 있지 않아서 아무 것도 하지 않았어요."
            return "조건이 맞지 않아서 아무 것도 하지 않았어요."

        if action == "turn_on":
            st2 = dev.on()
            return "조건이 맞아서 LED를 켰어요." if st2 is True else "조건이 맞아서 켜려 했는데 실패했어요."
        if action == "turn_off":
            st2 = dev.off()
            return "조건이 맞아서 LED를 껐어요." if st2 is False else "조건이 맞아서 끄려 했는데 실패했어요."
        if action == "toggle":
            st2 = toggle_led(dev)
            if st2 is True:
                return "조건이 맞아서 토글했어요. 지금은 켜짐."
            if st2 is False:
                return "조건이 맞아서 토글했어요. 지금은 꺼짐."
            return "조건이 맞아서 토글하려 했는데 실패했어요."

        return "조건부 명령인데 어떤 동작을 해야 할지(action) 해석이 애매해요."

    return reply or "무슨 뜻인지 애매해요. (켜기/끄기/상태확인/조건부 중 하나로 말해줘요)"


# =========================
# Main
# =========================
def main():
    dev = ArduinoClient(SERIAL_PORT, BAUD, TIMEOUT_S)
    print("Connected to Arduino (seq protocol). Type 'quit' to exit.\n")

    try:
        while True:
            user = input("You> ").strip()
            if user.lower() in ("quit", "exit"):
                break

            intent_obj = None
            try:
                intent_obj = ollama_parse_intent(user)
            except Exception as e:
                if not ENABLE_RULE_FALLBACK:
                    print(f"Bot> Ollama 호출 실패: {e}")
                    continue

            if intent_obj is None or not isinstance(intent_obj, dict) or "intent" not in intent_obj:
                intent_obj = rule_fallback_parse(user)

            try:
                out = handle_intent(dev, intent_obj)
                print(f"Bot> {out}")
            except Exception as e:
                print(f"Bot> 실행 중 오류: {e}")

    finally:
        dev.close()
        print("Bye.")


if __name__ == "__main__":
    main()
