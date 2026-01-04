// Arduino: LED on pin 13
const int LED_PIN = 13;
bool ledState = false;

String readLine() {
  static String buf = "";
  while (Serial.available()) {
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

void replyState() {
  Serial.print("STATE:");
  Serial.println(ledState ? "ON" : "OFF");
}

void setLed(bool on) {
  ledState = on;
  digitalWrite(LED_PIN, ledState ? HIGH : LOW);
  replyState();
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  Serial.begin(115200);
  delay(200);
  Serial.println("READY");
  replyState();
}

void loop() {
  String cmd = readLine();
  if (cmd.length() == 0) return;

  cmd.toUpperCase();

  if (cmd == "ON") setLed(true);
  else if (cmd == "OFF") setLed(false);
  else if (cmd == "STATUS") replyState();
  else {
    Serial.print("ERR:UNKNOWN_CMD:");
    Serial.println(cmd);
  }
}
