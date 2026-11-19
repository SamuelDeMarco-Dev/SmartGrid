/*
 * SmartGrid — firmware da placa de medição (Arduino UNO).
 *
 * Implementa o lado da placa do protocolo descrito em models/protocolo.py:
 *
 *   AGUARDANDO_LINK (9600 bps, silenciosa)
 *        | recebe  $HS,<baud>,<intervalo_ms>*CS
 *        | responde $ACK,HS,<baud>,<intervalo_ms>*CS  (ainda em 9600)
 *        v
 *   AGUARDANDO_SYNC (já na nova velocidade, por até TEMPO_SYNC_MS)
 *        | recebe  $SYNC*CS  ->  responde $ACK,SYNC*CS
 *        v
 *   TELEMETRIA: envia $TLM,<seq>,<tensao>,<corrente>,<disjuntor>*CS
 *        - $RELE,0|1  abre/fecha a carga  -> $ACK,RELE,0|1
 *        - $PING      mantém o link (watchdog de WATCHDOG_MS)
 *        - $BYE       ou silêncio do host -> volta a AGUARDANDO_LINK em 9600
 *
 * Ao abrir a porta, o PC pulsa o DTR e o UNO reinicia: a placa sempre
 * recomeça em AGUARDANDO_LINK, que é o que o protocolo exige.
 *
 * Ligações:
 *   A0  tensão   (potenciômetro 0–5 V, ou ZMPT101B com MODO_RMS = 1)
 *   A1  corrente (potenciômetro 0–5 V, ou ACS712 com MODO_RMS = 1)
 *   D2  botão de disparo da proteção (até o GND; usa INPUT_PULLUP)
 *   D7  relé da carga (ou LED + resistor)
 *   D13 LED da placa: pisca aguardando link, aceso com telemetria
 */

// ---------------------------------------------------------------------
// Calibração — ajuste na bancada
// ---------------------------------------------------------------------
#define MODO_RMS 0                  // 0: potenciômetros  1: ZMPT101B/ACS712

const float TENSAO_FUNDO_ESCALA = 250.0;   // V lidos com A0 em 5 V (MODO_RMS 0)
const float CORRENTE_FUNDO_ESCALA = 20.0;  // A lidos com A1 em 5 V (MODO_RMS 0)

const float FATOR_TENSAO_RMS = 0.55;       // V de rede por contagem do ADC (MODO_RMS 1)
const float SENSIBILIDADE_ACS712 = 0.185;  // V/A: 0.185 (5 A), 0.100 (20 A), 0.066 (30 A)
const int AMOSTRAS_RMS = 160;              // ~1 ciclo de 60 Hz por canal

const bool RELE_ATIVO_EM_LOW = false;      // módulos de relé comuns acionam em LOW

// ---------------------------------------------------------------------
// Protocolo
// ---------------------------------------------------------------------
const long VELOCIDADES[] = {9600, 19200, 38400, 115200};
const long VELOCIDADE_INICIAL = 9600;
const int INTERVALO_MINIMO_MS = 100;
const int INTERVALO_MAXIMO_MS = 5000;
const unsigned long TEMPO_SYNC_MS = 2000;
const unsigned long WATCHDOG_MS = 5000;

const byte PINO_TENSAO = A0;
const byte PINO_CORRENTE = A1;
const byte PINO_BOTAO = 2;
const byte PINO_RELE = 7;
const byte PINO_LED = LED_BUILTIN;

enum Estado { AGUARDANDO_LINK, AGUARDANDO_SYNC, TELEMETRIA };

Estado estado = AGUARDANDO_LINK;
long baudAtual = VELOCIDADE_INICIAL;
unsigned long intervaloMs = 1000;
unsigned long seq = 0;
bool releFechado = true;

unsigned long prazoSync = 0;
unsigned long ultimoHost = 0;
unsigned long proximaTelemetria = 0;
unsigned long ultimoPisca = 0;
unsigned long ultimoBotao = 0;

char linha[64];
byte tamanhoLinha = 0;

// ---------------------------------------------------------------------
// Enquadramento $CORPO*CS
// ---------------------------------------------------------------------
byte checksum(const char *corpo) {
  byte cs = 0;
  while (*corpo) cs ^= (byte)*corpo++;
  return cs;
}

void enviarPacote(const char *corpo) {
  char cs[3];
  sprintf(cs, "%02X", checksum(corpo));
  Serial.print('$');
  Serial.print(corpo);
  Serial.print('*');
  Serial.print(cs);
  Serial.print('\n');
}

// Valida o pacote em `texto` (sem o \n). Devolve o início do corpo e corta
// o texto no '*', ou NULL se o enquadramento ou o checksum estiverem errados.
char *validarPacote(char *texto) {
  if (texto[0] != '$') return NULL;
  char *asterisco = strrchr(texto, '*');
  if (asterisco == NULL || strlen(asterisco) < 3) return NULL;
  *asterisco = '\0';
  char *corpo = texto + 1;
  byte recebido = (byte)strtol(asterisco + 1, NULL, 16);
  return checksum(corpo) == recebido ? corpo : NULL;
}

bool velocidadeSuportada(long baud) {
  for (byte i = 0; i < sizeof(VELOCIDADES) / sizeof(VELOCIDADES[0]); i++) {
    if (VELOCIDADES[i] == baud) return true;
  }
  return false;
}

void trocarVelocidade(long baud) {
  Serial.flush();  // termina de transmitir o ACK na velocidade antiga
  Serial.end();
  Serial.begin(baud);
  baudAtual = baud;
}

void voltarAEsperar() {
  estado = AGUARDANDO_LINK;
  if (baudAtual != VELOCIDADE_INICIAL) trocarVelocidade(VELOCIDADE_INICIAL);
}

// ---------------------------------------------------------------------
// Relé e botão de proteção
// ---------------------------------------------------------------------
void aplicarRele(bool fechado) {
  releFechado = fechado;
  bool nivel = RELE_ATIVO_EM_LOW ? !fechado : fechado;
  digitalWrite(PINO_RELE, nivel ? HIGH : LOW);
}

void verificarBotao() {
  // Pressionado = LOW (pull-up interno). Debounce simples de 50 ms.
  if (digitalRead(PINO_BOTAO) == LOW && releFechado && millis() - ultimoBotao > 50) {
    ultimoBotao = millis();
    aplicarRele(false);  // disparo espontâneo: o desktop vê disjuntor = 0
  }
}

// ---------------------------------------------------------------------
// Medição
// ---------------------------------------------------------------------
#if MODO_RMS
float lerRms(byte pino, float fator) {
  long soma = 0;
  long somaQuadrados = 0;
  for (int i = 0; i < AMOSTRAS_RMS; i++) {
    int amostra = analogRead(pino);
    soma += amostra;
    somaQuadrados += (long)amostra * amostra;
  }
  float media = (float)soma / AMOSTRAS_RMS;
  float variancia = (float)somaQuadrados / AMOSTRAS_RMS - media * media;
  return variancia > 0 ? sqrt(variancia) * fator : 0.0;
}
#endif

float lerTensao() {
#if MODO_RMS
  return lerRms(PINO_TENSAO, FATOR_TENSAO_RMS);
#else
  return analogRead(PINO_TENSAO) * TENSAO_FUNDO_ESCALA / 1023.0;
#endif
}

float lerCorrente() {
  if (!releFechado) return 0.0;  // carga desligada
#if MODO_RMS
  return lerRms(PINO_CORRENTE, (5.0 / 1023.0) / SENSIBILIDADE_ACS712);
#else
  return analogRead(PINO_CORRENTE) * CORRENTE_FUNDO_ESCALA / 1023.0;
#endif
}

void enviarTelemetria() {
  char tensao[10];
  char corrente[10];
  char corpo[48];
  dtostrf(lerTensao(), 1, 1, tensao);  // AVR não formata float no sprintf
  dtostrf(lerCorrente(), 1, 3, corrente);
  seq++;
  snprintf(corpo, sizeof(corpo), "TLM,%lu,%s,%s,%d", seq, tensao, corrente,
           releFechado ? 1 : 0);
  enviarPacote(corpo);
}

// ---------------------------------------------------------------------
// Tratamento dos pacotes do desktop
// ---------------------------------------------------------------------
void tratarHandshake(char *campos) {
  char *textoBaud = strtok(campos, ",");
  char *textoIntervalo = strtok(NULL, ",");
  if (textoBaud == NULL || textoIntervalo == NULL || strtok(NULL, ",") != NULL) {
    enviarPacote("NAK,campos");
    return;
  }
  long baud = atol(textoBaud);
  long intervalo = atol(textoIntervalo);
  if (!velocidadeSuportada(baud)) {
    enviarPacote("NAK,velocidade");
    return;
  }
  if (intervalo < INTERVALO_MINIMO_MS || intervalo > INTERVALO_MAXIMO_MS) {
    enviarPacote("NAK,intervalo");
    return;
  }

  char corpo[40];
  snprintf(corpo, sizeof(corpo), "ACK,HS,%ld,%ld", baud, intervalo);
  enviarPacote(corpo);

  intervaloMs = intervalo;
  if (baud != baudAtual) trocarVelocidade(baud);
  estado = AGUARDANDO_SYNC;
  prazoSync = millis() + TEMPO_SYNC_MS;
}

void tratarPacote(char *corpo) {
  ultimoHost = millis();

  static char semCampos[] = "";
  char *virgula = strchr(corpo, ',');
  char *campos = semCampos;
  if (virgula != NULL) {
    *virgula = '\0';
    campos = virgula + 1;
  }

  switch (estado) {
    case AGUARDANDO_LINK:
      if (strcmp(corpo, "HS") == 0) tratarHandshake(campos);
      break;  // antes do handshake nada mais é aceito

    case AGUARDANDO_SYNC:
      if (strcmp(corpo, "SYNC") == 0) {
        enviarPacote("ACK,SYNC");
        estado = TELEMETRIA;
        proximaTelemetria = millis();
      }
      break;

    case TELEMETRIA:
      if (strcmp(corpo, "RELE") == 0 && (strcmp(campos, "0") == 0 || strcmp(campos, "1") == 0)) {
        aplicarRele(campos[0] == '1');
        enviarPacote(campos[0] == '1' ? "ACK,RELE,1" : "ACK,RELE,0");
      } else if (strcmp(corpo, "BYE") == 0) {
        voltarAEsperar();
      }
      // PING só renova ultimoHost
      break;
  }
}

void lerSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      linha[tamanhoLinha] = '\0';
      char *corpo = validarPacote(linha);
      if (corpo != NULL) tratarPacote(corpo);
      tamanhoLinha = 0;
    } else if (tamanhoLinha < sizeof(linha) - 1) {
      linha[tamanhoLinha++] = c;
    } else {
      tamanhoLinha = 0;  // linha longa demais: descarta
    }
  }
}

// ---------------------------------------------------------------------
void setup() {
  pinMode(PINO_BOTAO, INPUT_PULLUP);
  pinMode(PINO_RELE, OUTPUT);
  pinMode(PINO_LED, OUTPUT);
  aplicarRele(true);
  Serial.begin(VELOCIDADE_INICIAL);
}

void loop() {
  lerSerial();
  verificarBotao();

  // lerSerial() pode renovar ultimoHost: use um instante posterior para
  // evitar underflow sem sinal e um falso timeout no mesmo ciclo.
  unsigned long agora = millis();

  if (estado == AGUARDANDO_SYNC && (long)(agora - prazoSync) > 0) {
    voltarAEsperar();
  } else if (estado == TELEMETRIA) {
    if (agora - ultimoHost > WATCHDOG_MS) {
      voltarAEsperar();
    } else if ((long)(agora - proximaTelemetria) >= 0) {
      proximaTelemetria = agora + intervaloMs;
      enviarTelemetria();
    }
  }

  if (estado == TELEMETRIA) {
    digitalWrite(PINO_LED, HIGH);
  } else if (agora - ultimoPisca > 500) {
    ultimoPisca = agora;
    digitalWrite(PINO_LED, !digitalRead(PINO_LED));
  }
}
