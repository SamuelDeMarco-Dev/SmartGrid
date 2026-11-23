const byte PIN_OSCILOSCOPIO = 13;
const byte PIN_RELE = 12;
const byte PIN_BOTAO = 11;

const bool RELE_ACTIVE_LOW = true;

volatile int valorA0 = 0;
volatile int valorA1 = 0;
volatile bool statusRele = false;
volatile bool botaoPressionado = false;
volatile bool novosDados = false;
volatile bool estadoPino13 = false;

volatile bool conversaoHabilitada = false;

char bufferPayload[32];
char bufferCmd[16];
byte indiceCmd = 0;
bool gravandoCmd = false;

void acionarRele(bool ligar) {
  if (RELE_ACTIVE_LOW) {
    digitalWrite(PIN_RELE, ligar ? LOW : HIGH);
  } else {
    digitalWrite(PIN_RELE, ligar ? HIGH : LOW);
  }
}

void setup() {
  pinMode(PIN_OSCILOSCOPIO, OUTPUT);
  digitalWrite(PIN_OSCILOSCOPIO, LOW);

  pinMode(PIN_RELE, OUTPUT);
  acionarRele(false);

  pinMode(PIN_BOTAO, INPUT);

  Serial.begin(9600);

  noInterrupts();
  TCCR1A = 0;
  TCCR1B = 0;
  TCNT1  = 0;

  OCR1A = 1249;

  TCCR1B = (1 << WGM12) | (1 << CS11) | (1 << CS10);

  TIFR1 |= (1 << OCF1A);
  TIMSK1 |= (1 << OCIE1A);
  interrupts();

  delay(50);
  Serial.println(F("\n========================================"));
  Serial.println(F("PLACA EM ESPERA (9600 baud)"));
  Serial.println(F("Envie !S115200 para iniciar nessa taxa"));
  Serial.println(F("Envie !P para pausar e voltar para 9600"));
  Serial.println(F("========================================"));
}

ISR(TIMER1_COMPA_vect) {
  if (!conversaoHabilitada) {
    return;
  }

  estadoPino13 = !estadoPino13;
  digitalWrite(PIN_OSCILOSCOPIO, estadoPino13);

  valorA0 = analogRead(A0);
  valorA1 = analogRead(A1);
  statusRele = (digitalRead(PIN_RELE) == (RELE_ACTIVE_LOW ? LOW : HIGH));
  botaoPressionado = (digitalRead(PIN_BOTAO) == HIGH);

  novosDados = true;
}

void processarComando(char* cmd) {
  if (cmd[0] == 'S' || cmd[0] == 's') {
    unsigned long baudSolicitado = 115200;

    if (cmd[1] != '\0') {
      baudSolicitado = strtoul(&cmd[1], NULL, 10);
    }

    if (baudSolicitado == 9600 || baudSolicitado == 19200 || 
        baudSolicitado == 38400 || baudSolicitado == 115200) {
      
      Serial.print(F(">>> ACK: INICIANDO EM "));
      Serial.print(baudSolicitado);
      Serial.println(F(" BPS <<<"));
      Serial.flush();

      if (baudSolicitado != 9600) {
        Serial.end();
        delay(20);
        Serial.begin(baudSolicitado);
      }

      conversaoHabilitada = true;
    } else {
      Serial.println(F(">>> ERR: BAUD INVALIDO. Use 9600, 19200, 38400 ou 115200 <<<"));
    }
  }
  else if (strcasecmp(cmd, "P") == 0 || strcasecmp(cmd, "STOP") == 0) {
    conversaoHabilitada = false;
    estadoPino13 = false;
    digitalWrite(PIN_OSCILOSCOPIO, LOW);

    Serial.println(F("\n>>> ACK: CONVERSAO PAUSADA. RETORNANDO A 9600 BPS <<<"));
    Serial.flush();

    Serial.end();
    delay(20);
    Serial.begin(9600);
  }
  else if (strcmp(cmd, "1") == 0 || strcasecmp(cmd, "L") == 0) {
    acionarRele(true);
    Serial.println(F(">>> ACK: RELE LIGADO <<<"));
  }
  else if (strcmp(cmd, "0") == 0 || strcasecmp(cmd, "D") == 0) {
    acionarRele(false);
    Serial.println(F(">>> ACK: RELE DESLIGADO <<<"));
  }
  else if (strcasecmp(cmd, "T") == 0) {
    bool estadoAtual = (digitalRead(PIN_RELE) == (RELE_ACTIVE_LOW ? LOW : HIGH));
    acionarRele(!estadoAtual);
    Serial.println(F(">>> ACK: RELE ALTERNADO <<<"));
  }
}

void loop() {
  while (Serial.available() > 0) {
    char c = Serial.read();

    if (c == '!') {
      gravandoCmd = true;
      indiceCmd = 0;
      bufferCmd[0] = '\0';
    } 
    else if (gravandoCmd) {
      if (c == '\n' || c == '\r') {
        if (indiceCmd > 0) {
          bufferCmd[indiceCmd] = '\0';
          processarComando(bufferCmd);
        }
        gravandoCmd = false;
        indiceCmd = 0;
      } 
      else if (indiceCmd < sizeof(bufferCmd) - 1) {
        if (c != ' ') {
          bufferCmd[indiceCmd++] = c;
          bufferCmd[indiceCmd] = '\0';
        }
      }
    }
  }

  if (conversaoHabilitada && novosDados) {
    int copiaA0, copiaA1;
    bool copiaRele, copiaBotao;

    noInterrupts();
    copiaA0 = valorA0;
    copiaA1 = valorA1;
    copiaRele = statusRele;
    copiaBotao = botaoPressionado;
    novosDados = false;
    interrupts();

    snprintf(bufferPayload, sizeof(bufferPayload), "DAT,%d,%d,%d,%d",
             copiaA0, copiaA1, copiaRele ? 1 : 0, copiaBotao ? 1 : 0);

    byte checksum = 0;
    for (byte i = 0; bufferPayload[i] != '\0'; i++) {
      checksum ^= (byte)bufferPayload[i];
    }

    Serial.print('$');
    Serial.print(bufferPayload);
    Serial.print('*');
    if (checksum < 0x10) Serial.print('0');
    Serial.println(checksum, HEX);
  }
}