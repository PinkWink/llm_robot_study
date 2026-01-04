/*
  Arduino LED(13) controller with sequence-numbered protocol.

  PC -> Arduino (newline '\n' terminated):
    - ON <seq>
    - OFF <seq>
    - STATUS <seq>

  Arduino -> PC (newline '\n' terminated):
    - READY
    - STATE <seq> ON
    - STATE <seq> OFF
    - ERR <seq> UNKNOWN_CMD <cmd>
    - ERR <seq> BAD_FORMAT

  Notes:
    - Always include <seq> in responses for ON/OFF/STATUS.
    - READY is only for boot; PC can ignore it.
*/

const int LED_PIN = 13;
bool ledState = false;

String readLine() {
  static String buf = "";
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n') {
      String line = buf;
      buf = "";
      line.trim();
      return line;
    } else if (c != '\r') {
      buf += c;
    }
  }
  return "";
}

void replyState(long seq) {
  Serial.print("STATE ");
  Serial.print(seq);
  Serial.print(" ");
  Serial.println(ledState ? "ON" : "OFF");
}

void replyErr(long seq, const String& code, const String& detail) {
  Serial.print("ERR ");
  Serial.print(seq);
  Serial.print(" ");
  Serial.print(code);
  if (detail.length() > 0) {
    Serial.print(" ");
    Serial.print(detail);
  }
  Serial.println();
}

bool parseLong(const String& s, long& out) {
  if (s.length() == 0) return false;
  for (unsigned int i = 0; i < s.length(); i++) {
    char c = s[i];
    if (!(c == '-' || (c >= '0' && c <= '9'))) return false;
  }
  out = s.toInt();
  return true;
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  ledState = false;

  Serial.begin(115200);
  delay(200);

  Serial.println("READY");
}

void loop() {
  String line = readLine();
  if (line.length() == 0) return;

  // Split by spaces (at most 2 tokens expected: CMD and SEQ)
  int sp = line.indexOf(' ');
  if (sp < 0) {
    // No seq provided
    replyErr(-1, "BAD_FORMAT", "");
    return;
  }

  String cmd = line.substring(0, sp);
  String seqStr = line.substring(sp + 1);
  cmd.trim();
  seqStr.trim();
  cmd.toUpperCase();

  long seq = 0;
  if (!parseLong(seqStr, seq)) {
    replyErr(-1, "BAD_FORMAT", "");
    return;
  }

  if (cmd == "ON") {
    ledState = true;
    digitalWrite(LED_PIN, HIGH);
    replyState(seq);
  } else if (cmd == "OFF") {
    ledState = false;
    digitalWrite(LED_PIN, LOW);
    replyState(seq);
  } else if (cmd == "STATUS") {
    replyState(seq);
  } else {
    replyErr(seq, "UNKNOWN_CMD", cmd);
  }
}
