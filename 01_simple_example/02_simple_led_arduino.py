import json
import time
import requests
import serial

# =========================
# Config
# =========================
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "phi3:mini"  # 원하면 다른 모델로 바꿔도 됨
SERIAL_PORT = "/dev/tty.usbmodem21101"  #"/dev/ttyACM0"
BAUD = 115200
TIMEOUT_S = 1.0

SYSTEM_PROMPT = """너는 아두이노 LED(13번 핀) 제어를 위한 의도 파서다.
사용자 문장을 보고 반드시 아래 JSON 하나만 출력한다(설명 금지).

스키마:
{
    "intent": "turn_on" | "turn_off" | "get_status" | "unknown",
    "reply": "사용자에게 보여줄 한글 한 문장"
}

규칙:
- LED 켜기 의미면 intent=turn_on
- LED 끄기 의미면 intent=turn_off
- LED 상태 질문이면 intent=get_status
- 애매하면 intent=unknown
"""

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

def open_serial():
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=TIMEOUT_S)
    time.sleep(1.5)  # 아두이노 리셋 대기
    # 버퍼 비우기
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    return ser

def arduino_cmd(ser: serial.Serial, cmd: str) -> str:
    ser.write((cmd.strip() + "\n").encode("utf-8"))
    ser.flush()
    # 한 줄 응답을 기대
    line = ser.readline().decode("utf-8", errors="ignore").strip()
    return line

def main():
    ser = open_serial()
    print("Connected. Type 'quit' to exit.")

    while True:
        user = input("\nYou> ").strip()
        if user.lower() in ("quit", "exit"):
            break

        intent_obj = ollama_parse_intent(user)
        intent = intent_obj.get("intent", "unknown")
        fallback_reply = intent_obj.get("reply", "")

        if intent == "turn_on":
            resp = arduino_cmd(ser, "ON")
            print(f"Bot> LED 켰어요. ({resp})")

        elif intent == "turn_off":
            resp = arduino_cmd(ser, "OFF")
            print(f"Bot> LED 껐어요. ({resp})")

        elif intent == "get_status":
            resp = arduino_cmd(ser, "STATUS")
            # resp: "STATE:ON" / "STATE:OFF"
            if "STATE:ON" in resp:
                print("Bot> 지금 LED는 켜져 있어요.")
            elif "STATE:OFF" in resp:
                print("Bot> 지금 LED는 꺼져 있어요.")
            else:
                print(f"Bot> 상태를 읽는 중 이상한 응답이 왔어요: {resp}")

        else:
            # 애매하면 모델이 만든 reply 사용
            print(f"Bot> {fallback_reply or '무슨 뜻인지 애매해요. LED를 켤까요, 끌까요, 상태를 물어보신 걸까요?'}")

    ser.close()

if __name__ == "__main__":
    main()
