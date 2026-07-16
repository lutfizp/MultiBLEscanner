#include <Arduino.h>
#include <ArduinoJson.h>
#include <NimBLEDevice.h>

#include "config.h"

struct ScannerConfig {
  uint32_t scanIntervalMs = SCAN_INTERVAL_MS;
  uint32_t uploadIntervalMs = UPLOAD_INTERVAL_MS;
  uint16_t maxBatchSize = MAX_UPLOAD_BATCH_SIZE;
  int rssiMin = DEFAULT_RSSI_MIN_DBM;
  uint32_t configVersion = 1;
};

struct Observation {
  String observationId;
  String observedAt;
  String timeSource;
  String bootId;
  uint32_t monotonicMs = 0;
  uint32_t scanCycle = 0;
  uint32_t clockSyncAgeMs = 0;
  String address;
  String addressType;
  String advertisedName;
  int rssi = 0;
  int txPower = 0;
  bool hasTxPower = false;
  String advertisingType;
  String serviceUuids;
  String manufacturerDataHex;
  String rawAdvertisingPayloadHex;
  String rawScanResponsePayloadHex;
  size_t packetLength = 0;
  size_t advertisingPacketLength = 0;
  size_t scanResponsePacketLength = 0;
  bool connectable = false;
  bool gattAttempted = false;
  String gattStatus;
  String gattErrorCode;
  String gattDeviceName;
  String gattManufacturerName;
  String gattModelNumber;
  String gattSerialNumber;
  String gattFirmwareRevision;
  String gattHardwareRevision;
  String gattSoftwareRevision;
  String gattSystemIdHex;
  String gattPnpIdHex;
  String gattDiscoveredServices;
  String gattCharacteristicValues;
  uint32_t gattAttemptDurationMs = 0;
};

struct EnrichmentTarget {
  String address;
  uint8_t addressType = 0;
  int rssi = -127;
  bool hasAdvertisedName = false;
};

struct EnrichmentCacheEntry {
  String address;
  uint8_t addressType = 0;
};

ScannerConfig scannerConfig;
Observation observationBuffer[OBSERVATION_BUFFER_SIZE];
size_t bufferHead = 0;
size_t bufferTail = 0;
size_t bufferCount = 0;
uint32_t droppedObservations = 0;
uint32_t observationCounter = 0;
uint32_t lastScanAt = 0;
uint32_t lastUploadAt = 0;
uint32_t lastHeartbeatAt = 0;
uint32_t lastConfigAt = 0;
uint32_t scanCycle = 0;
uint32_t batchSequence = 0;
uint32_t lastClockSyncAt = 0;
int64_t clockOffsetMs = 0;
bool clockSynchronized = false;
String bootId;
bool bridgeResponseReady = false;
int bridgeResponseStatus = 0;
String pendingBatchId;
uint32_t pendingBatchSequence = 0;
size_t pendingBatchSize = 0;
EnrichmentTarget enrichmentQueue[GATT_ENRICHMENT_QUEUE_SIZE];
size_t enrichmentQueueCount = 0;
EnrichmentCacheEntry enrichmentCache[GATT_ENRICHMENT_CACHE_SIZE];
size_t enrichmentCacheCount = 0;
size_t enrichmentCacheCursor = 0;

const char *TIME_SYNC_PREFIX = "@@BT_SCANNER_TIME@@";
const char *BRIDGE_ACK_PREFIX = "@@BT_SCANNER_ACK@@";
const char *BRIDGE_CONFIG_PREFIX = "@@BT_SCANNER_CONFIG@@";

String bytesToHex(const uint8_t *data, size_t length) {
  static const char *hex = "0123456789abcdef";
  String output;
  output.reserve(length * 2);
  for (size_t i = 0; i < length; i++) {
    output += hex[(data[i] >> 4) & 0x0F];
    output += hex[data[i] & 0x0F];
  }
  return output;
}

String stringToHex(const std::string &value) {
  return bytesToHex(reinterpret_cast<const uint8_t *>(value.data()), value.length());
}

bool isValidUtf8(const uint8_t *data, size_t length) {
  size_t index = 0;
  while (index < length) {
    uint8_t first = data[index];
    if (first <= 0x7F) {
      if (first < 0x20 && first != '\t') {
        return false;
      }
      index++;
      continue;
    }
    size_t continuationCount = 0;
    uint32_t codepoint = 0;
    if ((first & 0xE0) == 0xC0) {
      continuationCount = 1;
      codepoint = first & 0x1F;
    } else if ((first & 0xF0) == 0xE0) {
      continuationCount = 2;
      codepoint = first & 0x0F;
    } else if ((first & 0xF8) == 0xF0) {
      continuationCount = 3;
      codepoint = first & 0x07;
    } else {
      return false;
    }
    if (index + continuationCount >= length) {
      return false;
    }
    for (size_t offset = 1; offset <= continuationCount; offset++) {
      uint8_t next = data[index + offset];
      if ((next & 0xC0) != 0x80) {
        return false;
      }
      codepoint = (codepoint << 6) | (next & 0x3F);
    }
    if ((continuationCount == 1 && codepoint < 0x80) ||
        (continuationCount == 2 && codepoint < 0x800) ||
        (continuationCount == 3 && codepoint < 0x10000) ||
        codepoint > 0x10FFFF ||
        (codepoint >= 0xD800 && codepoint <= 0xDFFF)) {
      return false;
    }
    index += continuationCount + 1;
  }
  return true;
}

String gattTextValue(const NimBLEAttValue &value) {
  size_t length = value.size();
  while (length > 0 && value.data()[length - 1] == 0) {
    length--;
  }
  if (length == 0 || !isValidUtf8(value.data(), length)) {
    return String("");
  }
  String output;
  output.reserve(length);
  for (size_t index = 0; index < length; index++) {
    output += static_cast<char>(value.data()[index]);
  }
  output.trim();
  return output;
}

uint64_t epochNowMs() {
  if (!clockSynchronized) {
    return 0;
  }
  int64_t epochMs = static_cast<int64_t>(millis()) + clockOffsetMs;
  if (epochMs < 1700000000000LL) {
    return 0;
  }
  return static_cast<uint64_t>(epochMs);
}

String isoNow() {
  uint64_t epochMs = epochNowMs();
  if (epochMs == 0) {
    return String("");
  }
  time_t now = static_cast<time_t>(epochMs / 1000ULL);
  struct tm timeInfo;
  gmtime_r(&now, &timeInfo);
  char secondsBuffer[21];
  char buffer[30];
  strftime(secondsBuffer, sizeof(secondsBuffer), "%Y-%m-%dT%H:%M:%S", &timeInfo);
  snprintf(buffer, sizeof(buffer), "%s.%03lluZ", secondsBuffer, epochMs % 1000ULL);
  return String(buffer);
}

const char *timeSource() {
  return clockSynchronized ? "usb_host_synchronized" : "unsynchronized";
}

uint32_t clockSyncAgeMs() {
  return clockSynchronized ? millis() - lastClockSyncAt : 0;
}

void applyTimeSync(const String &value) {
  char *end = nullptr;
  long long epochMs = strtoll(value.c_str(), &end, 10);
  if (end == value.c_str() || *end != '\0' || epochMs < 1700000000000LL) {
    return;
  }
  clockOffsetMs = static_cast<int64_t>(epochMs) - static_cast<int64_t>(millis());
  lastClockSyncAt = millis();
  clockSynchronized = true;
}

void applyConfig(const String &payload) {
  DynamicJsonDocument doc(1024);
  if (deserializeJson(doc, payload)) {
    return;
  }

  uint32_t version = doc["version"] | scannerConfig.configVersion;
  uint32_t scanIntervalMs = doc["scan_interval_ms"] | scannerConfig.scanIntervalMs;
  uint32_t uploadIntervalSeconds = doc["upload_interval_seconds"] | (scannerConfig.uploadIntervalMs / 1000);
  uint16_t batchSize = doc["batch_size"] | scannerConfig.maxBatchSize;
  int rssiMin = doc["rssi_min"] | scannerConfig.rssiMin;

  if (scanIntervalMs >= SCAN_DURATION_SECONDS * 1000UL && scanIntervalMs <= 60000UL) {
    scannerConfig.scanIntervalMs = scanIntervalMs;
  }
  if (uploadIntervalSeconds >= 1 && uploadIntervalSeconds <= 60) {
    scannerConfig.uploadIntervalMs = uploadIntervalSeconds * 1000UL;
  }
  if (batchSize >= 1 && batchSize <= MAX_UPLOAD_BATCH_SIZE) {
    scannerConfig.maxBatchSize = batchSize;
  }
  if (rssiMin >= -110 && rssiMin <= -20) {
    scannerConfig.rssiMin = rssiMin;
  }
  scannerConfig.configVersion = version;
}

void pollSerialControl() {
  static String command;
  while (Serial.available() > 0) {
    char character = static_cast<char>(Serial.read());
    if (character == '\r') {
      continue;
    }
    if (character == '\n') {
      if (command.startsWith(TIME_SYNC_PREFIX)) {
        applyTimeSync(command.substring(strlen(TIME_SYNC_PREFIX)));
      } else if (command.startsWith(BRIDGE_CONFIG_PREFIX)) {
        applyConfig(command.substring(strlen(BRIDGE_CONFIG_PREFIX)));
      } else if (command.startsWith(BRIDGE_ACK_PREFIX)) {
        char *end = nullptr;
        long status = strtol(command.substring(strlen(BRIDGE_ACK_PREFIX)).c_str(), &end, 10);
        if (end != nullptr && *end == '\0') {
          bridgeResponseStatus = static_cast<int>(status);
          bridgeResponseReady = true;
        }
      }
      command = "";
      continue;
    }
    if (command.length() < SERIAL_CONTROL_MAX_BYTES) {
      command += character;
    } else {
      command = "";
    }
  }
}

String makeId(const char *prefix) {
  observationCounter++;
  return String(prefix) + "-" + SCANNER_ID + "-" + String(millis()) + "-" + String(observationCounter);
}

void pushObservation(const Observation &observation) {
  if (bufferCount == OBSERVATION_BUFFER_SIZE) {
    // The oldest queued observation is being discarded, so the retry batch no
    // longer represents a stable payload and must receive a new batch ID.
    pendingBatchId = "";
    pendingBatchSize = 0;
    bufferTail = (bufferTail + 1) % OBSERVATION_BUFFER_SIZE;
    bufferCount--;
    droppedObservations++;
  }
  observationBuffer[bufferHead] = observation;
  bufferHead = (bufferHead + 1) % OBSERVATION_BUFFER_SIZE;
  bufferCount++;
}

void popObservations(size_t count) {
  size_t removeCount = min(count, bufferCount);
  bufferTail = (bufferTail + removeCount) % OBSERVATION_BUFFER_SIZE;
  bufferCount -= removeCount;
}

bool sameEnrichmentTarget(const String &address, uint8_t addressType, const EnrichmentCacheEntry &entry) {
  return entry.address == address && entry.addressType == addressType;
}

bool enrichmentWasAttempted(const String &address, uint8_t addressType) {
  for (size_t index = 0; index < enrichmentCacheCount; index++) {
    if (sameEnrichmentTarget(address, addressType, enrichmentCache[index])) {
      return true;
    }
  }
  return false;
}

void rememberEnrichmentAttempt(const String &address, uint8_t addressType) {
  EnrichmentCacheEntry entry;
  entry.address = address;
  entry.addressType = addressType;
  if (enrichmentCacheCount < GATT_ENRICHMENT_CACHE_SIZE) {
    enrichmentCache[enrichmentCacheCount++] = entry;
    return;
  }
  enrichmentCache[enrichmentCacheCursor] = entry;
  enrichmentCacheCursor = (enrichmentCacheCursor + 1) % GATT_ENRICHMENT_CACHE_SIZE;
}

int enrichmentPriority(const EnrichmentTarget &target) {
  return target.rssi + (target.hasAdvertisedName ? 0 : 30);
}

void queueEnrichmentTarget(const String &address, uint8_t addressType, int rssi, bool hasAdvertisedName) {
  if (enrichmentWasAttempted(address, addressType)) {
    return;
  }
  for (size_t index = 0; index < enrichmentQueueCount; index++) {
    if (enrichmentQueue[index].address == address && enrichmentQueue[index].addressType == addressType) {
      enrichmentQueue[index].rssi = max(enrichmentQueue[index].rssi, rssi);
      enrichmentQueue[index].hasAdvertisedName = enrichmentQueue[index].hasAdvertisedName || hasAdvertisedName;
      return;
    }
  }

  EnrichmentTarget target;
  target.address = address;
  target.addressType = addressType;
  target.rssi = rssi;
  target.hasAdvertisedName = hasAdvertisedName;
  if (enrichmentQueueCount < GATT_ENRICHMENT_QUEUE_SIZE) {
    enrichmentQueue[enrichmentQueueCount++] = target;
    return;
  }

  size_t weakestIndex = 0;
  for (size_t index = 1; index < enrichmentQueueCount; index++) {
    if (enrichmentPriority(enrichmentQueue[index]) < enrichmentPriority(enrichmentQueue[weakestIndex])) {
      weakestIndex = index;
    }
  }
  if (enrichmentPriority(target) > enrichmentPriority(enrichmentQueue[weakestIndex])) {
    enrichmentQueue[weakestIndex] = target;
  }
}

bool takeNextEnrichmentTarget(EnrichmentTarget &target) {
  if (enrichmentQueueCount == 0) {
    return false;
  }
  size_t strongestIndex = 0;
  for (size_t index = 1; index < enrichmentQueueCount; index++) {
    if (enrichmentPriority(enrichmentQueue[index]) > enrichmentPriority(enrichmentQueue[strongestIndex])) {
      strongestIndex = index;
    }
  }
  target = enrichmentQueue[strongestIndex];
  enrichmentQueue[strongestIndex] = enrichmentQueue[enrichmentQueueCount - 1];
  enrichmentQueueCount--;
  return true;
}

Observation *latestBufferedObservation(const String &address) {
  for (size_t offset = 0; offset < bufferCount; offset++) {
    size_t index = (bufferHead + OBSERVATION_BUFFER_SIZE - 1 - offset) % OBSERVATION_BUFFER_SIZE;
    if (observationBuffer[index].address == address) {
      return &observationBuffer[index];
    }
  }
  return nullptr;
}

void appendCsvValue(String &list, const String &value) {
  if (value.length() == 0) {
    return;
  }
  if (list.length() > 0) {
    list += ",";
  }
  list += value;
}

void appendGattCharacteristic(Observation &observation, const char *uuid, const String &hexValue) {
  if (hexValue.length() == 0) {
    return;
  }
  if (observation.gattCharacteristicValues.length() > 0) {
    observation.gattCharacteristicValues += ",";
  }
  observation.gattCharacteristicValues += uuid;
  observation.gattCharacteristicValues += "=";
  observation.gattCharacteristicValues += hexValue;
}

bool readGattCharacteristic(
  NimBLEClient *client,
  const char *serviceUuid,
  const char *characteristicUuid,
  Observation &observation,
  String *textValue,
  String *binaryHexValue = nullptr
) {
  NimBLERemoteService *service = client->getService(serviceUuid);
  if (service == nullptr) {
    return false;
  }
  NimBLERemoteCharacteristic *characteristic = service->getCharacteristic(characteristicUuid);
  if (characteristic == nullptr || !characteristic->canRead()) {
    return false;
  }
  NimBLEAttValue value = characteristic->readValue();
  if (value.size() == 0) {
    return false;
  }
  String rawHex = bytesToHex(value.data(), value.size());
  appendGattCharacteristic(observation, characteristicUuid, rawHex);
  if (textValue != nullptr) {
    *textValue = gattTextValue(value);
  }
  if (binaryHexValue != nullptr) {
    *binaryHexValue = rawHex;
  }
  return true;
}

void enrichNextTarget() {
  EnrichmentTarget target;
  if (!takeNextEnrichmentTarget(target)) {
    return;
  }
  rememberEnrichmentAttempt(target.address, target.addressType);
  Observation *observation = latestBufferedObservation(target.address);
  if (observation == nullptr) {
    return;
  }

  observation->gattAttempted = true;
  uint32_t startedAt = millis();
  NimBLEClient *client = NimBLEDevice::createClient();
  if (client == nullptr) {
    observation->gattStatus = "connection_failed";
    observation->gattErrorCode = "client_allocation_failed";
    observation->gattAttemptDurationMs = millis() - startedAt;
    return;
  }

  client->setConnectTimeout(GATT_CONNECT_TIMEOUT_SECONDS);
  client->setConnectionParams(24, 40, 0, 60);
  NimBLEAddress peerAddress(std::string(target.address.c_str()), target.addressType);
  if (!client->connect(peerAddress)) {
    observation->gattStatus = "connection_failed";
    observation->gattErrorCode = String("nimble_") + String(client->getLastError());
    observation->gattAttemptDurationMs = millis() - startedAt;
    NimBLEDevice::deleteClient(client);
    return;
  }

  std::vector<NimBLERemoteService *> *services = client->getServices(true);
  if (services != nullptr) {
    for (NimBLERemoteService *service : *services) {
      if (service != nullptr) {
        appendCsvValue(observation->gattDiscoveredServices, String(service->getUUID().toString().c_str()));
      }
    }
  }

  size_t readCount = 0;
  readCount += readGattCharacteristic(client, "1800", "2a00", *observation, &observation->gattDeviceName);
  String ignoredText;
  readCount += readGattCharacteristic(client, "1800", "2a01", *observation, &ignoredText);
  readCount += readGattCharacteristic(client, "180a", "2a23", *observation, nullptr, &observation->gattSystemIdHex);
  readCount += readGattCharacteristic(client, "180a", "2a24", *observation, &observation->gattModelNumber);
  readCount += readGattCharacteristic(client, "180a", "2a25", *observation, &observation->gattSerialNumber);
  readCount += readGattCharacteristic(client, "180a", "2a26", *observation, &observation->gattFirmwareRevision);
  readCount += readGattCharacteristic(client, "180a", "2a27", *observation, &observation->gattHardwareRevision);
  readCount += readGattCharacteristic(client, "180a", "2a28", *observation, &observation->gattSoftwareRevision);
  readCount += readGattCharacteristic(client, "180a", "2a29", *observation, &observation->gattManufacturerName);
  readCount += readGattCharacteristic(client, "180a", "2a2a", *observation, &ignoredText);
  readCount += readGattCharacteristic(client, "180a", "2a50", *observation, nullptr, &observation->gattPnpIdHex);

  if (services == nullptr || services->empty()) {
    observation->gattStatus = "service_discovery_failed";
    observation->gattErrorCode = String("nimble_") + String(client->getLastError());
  } else if (readCount == 0) {
    int lastError = client->getLastError();
    observation->gattStatus = (lastError == 5 || lastError == 8 || lastError == 15)
      ? "security_required"
      : "partial";
    observation->gattErrorCode = lastError == 0
      ? "no_readable_identity_characteristics"
      : String("nimble_") + String(lastError);
  } else {
    observation->gattStatus = "success";
  }
  observation->gattAttemptDurationMs = millis() - startedAt;
  client->disconnect();
  NimBLEDevice::deleteClient(client);
}

bool waitForBridgeResponse(String &responseBody) {
  uint32_t startedAt = millis();
  while (!bridgeResponseReady && millis() - startedAt < SERIAL_BRIDGE_RESPONSE_TIMEOUT_MS) {
    pollSerialControl();
    delay(5);
  }
  responseBody = String(bridgeResponseStatus);
  return bridgeResponseReady && bridgeResponseStatus >= 200 && bridgeResponseStatus < 300;
}

bool httpRequest(const char *method, const String &path, const String &body, String &responseBody) {
  bridgeResponseReady = false;
  bridgeResponseStatus = 0;
  Serial.println("|||BRIDGE_START|||");
  Serial.println(method);
  Serial.println(path);
  Serial.println(body);
  Serial.println("|||BRIDGE_END|||");
  Serial.flush();
  return waitForBridgeResponse(responseBody);
}

bool httpRequestJson(const char *method, const String &path, JsonDocument &document, String &responseBody) {
  bridgeResponseReady = false;
  bridgeResponseStatus = 0;
  Serial.println("|||BRIDGE_START|||");
  Serial.println(method);
  Serial.println(path);
  // Stream the document directly. Keeping a second 29 KB String copy here
  // can exhaust the ESP32 heap and emit a truncated JSON body.
  serializeJson(document, Serial);
  Serial.println();
  Serial.println("|||BRIDGE_END|||");
  Serial.flush();
  return waitForBridgeResponse(responseBody);
}

void fetchConfig() {
  String response;
  httpRequest("GET", String("/api/scanners/") + SCANNER_ID + "/config", "", response);
}

void sendHeartbeat() {
  DynamicJsonDocument doc(1536);
  doc["message_id"] = makeId("hb");
  doc["scanner_time"] = isoNow();
  doc["uptime_seconds"] = millis() / 1000;
  doc["firmware_version"] = FIRMWARE_VERSION;
  doc["config_version"] = scannerConfig.configVersion;
  doc["config_status"] = "applied";
  JsonObject health = doc.createNestedObject("health");
  health["free_heap"] = ESP.getFreeHeap();
  health["min_free_heap"] = ESP.getMinFreeHeap();
  health["boot_id"] = bootId;
  health["monotonic_ms"] = millis();
  health["time_source"] = timeSource();
  health["clock_sync_age_ms"] = clockSyncAgeMs();
  health["clock_synchronized"] = clockSynchronized;

  String body;
  serializeJson(doc, body);
  String response;
  httpRequest("POST", String("/api/scanners/") + SCANNER_ID + "/heartbeat", body, response);
}

bool uploadBatch() {
  if (bufferCount == 0) {
    return true;
  }

  if (pendingBatchId.length() == 0) {
    pendingBatchSize = min<size_t>(
      min<size_t>(bufferCount, scannerConfig.maxBatchSize),
      MAX_SERIAL_FRAME_OBSERVATIONS
    );
    pendingBatchId = makeId("batch");
    pendingBatchSequence = ++batchSequence;
  }
  size_t batchSize = min<size_t>(bufferCount, pendingBatchSize);
  if (batchSize == 0) {
    pendingBatchId = "";
    pendingBatchSize = 0;
    return true;
  }
  // Allocate for the frame being sent instead of reserving 64 KB on every
  // upload. A fixed 64 KB allocation becomes unavailable after NimBLE and the
  // bounded observation buffer occupy their normal runtime heap.
  size_t documentCapacity = UPLOAD_JSON_BASE_CAPACITY
    + batchSize * UPLOAD_JSON_PER_OBSERVATION_CAPACITY;
  DynamicJsonDocument doc(documentCapacity);
  doc["batch_id"] = pendingBatchId;
  doc["batch_sequence"] = pendingBatchSequence;
  doc["boot_id"] = bootId;
  doc["sent_at"] = isoNow();
  doc["scanner_time"] = isoNow();
  doc["time_source"] = timeSource();
  doc["clock_sync_age_ms"] = clockSyncAgeMs();
  doc["firmware_version"] = FIRMWARE_VERSION;
  doc["scanner_uptime_seconds"] = millis() / 1000;
  doc["dropped_observations"] = droppedObservations;

  JsonObject network = doc.createNestedObject("network_state");
  network["transport"] = "usb_serial";
  network["clock_synchronized"] = clockSynchronized;

  JsonArray observations = doc.createNestedArray("observations");
  for (size_t i = 0; i < batchSize; i++) {
    const Observation &item = observationBuffer[(bufferTail + i) % OBSERVATION_BUFFER_SIZE];
    JsonObject obs = observations.createNestedObject();
    obs["observation_id"] = item.observationId;
    obs["observed_at"] = item.observedAt;
    obs["scanner_time"] = item.observedAt;
    obs["time_source"] = item.timeSource;
    obs["boot_id"] = item.bootId;
    obs["monotonic_ms"] = item.monotonicMs;
    obs["scan_cycle"] = item.scanCycle;
    obs["clock_sync_age_ms"] = item.clockSyncAgeMs;
    obs["address"] = item.address;
    obs["address_type"] = item.addressType;
    obs["advertised_name"] = item.advertisedName;
    obs["local_name"] = item.advertisedName;
    obs["rssi"] = item.rssi;
    if (item.hasTxPower) {
      obs["tx_power"] = item.txPower;
    }
    obs["advertising_type"] = item.advertisingType;
    JsonArray services = obs.createNestedArray("service_uuids");
    if (item.serviceUuids.length() > 0) {
      int start = 0;
      while (start < item.serviceUuids.length()) {
        int comma = item.serviceUuids.indexOf(',', start);
        if (comma < 0) {
          services.add(item.serviceUuids.substring(start));
          break;
        }
        services.add(item.serviceUuids.substring(start, comma));
        start = comma + 1;
      }
    }
    obs.createNestedObject("service_data");
    if (item.manufacturerDataHex.length()) {
      obs["manufacturer_data"] = item.manufacturerDataHex;
    } else {
      obs["manufacturer_data"] = nullptr;
    }
    obs["appearance"] = nullptr;
    obs.createNestedObject("advertising_flags");
    obs["connectable"] = item.connectable;
    if (item.rawAdvertisingPayloadHex.length()) {
      obs["raw_advertising_payload"] = item.rawAdvertisingPayloadHex;
    } else {
      obs["raw_advertising_payload"] = nullptr;
    }
    if (item.rawScanResponsePayloadHex.length()) {
      obs["raw_scan_response_payload"] = item.rawScanResponsePayloadHex;
    } else {
      obs["raw_scan_response_payload"] = nullptr;
    }
    obs["packet_length"] = item.packetLength;
    obs["advertising_packet_length"] = item.advertisingPacketLength;
    obs["scan_response_packet_length"] = item.scanResponsePacketLength;
    obs["payload_layout_version"] = 2;
    if (item.gattAttempted) {
      JsonObject gatt = obs.createNestedObject("gatt_enrichment");
      gatt["status"] = item.gattStatus;
      if (item.gattErrorCode.length()) {
        gatt["error_code"] = item.gattErrorCode;
      }
      if (item.gattDeviceName.length()) {
        gatt["device_name"] = item.gattDeviceName;
      }
      if (item.gattManufacturerName.length()) {
        gatt["manufacturer_name"] = item.gattManufacturerName;
      }
      if (item.gattModelNumber.length()) {
        gatt["model_number"] = item.gattModelNumber;
      }
      if (item.gattSerialNumber.length()) {
        gatt["serial_number"] = item.gattSerialNumber;
      }
      if (item.gattFirmwareRevision.length()) {
        gatt["firmware_revision"] = item.gattFirmwareRevision;
      }
      if (item.gattHardwareRevision.length()) {
        gatt["hardware_revision"] = item.gattHardwareRevision;
      }
      if (item.gattSoftwareRevision.length()) {
        gatt["software_revision"] = item.gattSoftwareRevision;
      }
      if (item.gattSystemIdHex.length()) {
        gatt["system_id"] = item.gattSystemIdHex;
      }
      if (item.gattPnpIdHex.length()) {
        gatt["pnp_id"] = item.gattPnpIdHex;
      }
      gatt["attempt_duration_ms"] = item.gattAttemptDurationMs;

      JsonArray gattServices = gatt.createNestedArray("discovered_services");
      int serviceStart = 0;
      while (serviceStart < item.gattDiscoveredServices.length()) {
        int comma = item.gattDiscoveredServices.indexOf(',', serviceStart);
        if (comma < 0) {
          gattServices.add(item.gattDiscoveredServices.substring(serviceStart));
          break;
        }
        gattServices.add(item.gattDiscoveredServices.substring(serviceStart, comma));
        serviceStart = comma + 1;
      }

      JsonObject characteristicValues = gatt.createNestedObject("characteristic_values");
      int valueStart = 0;
      while (valueStart < item.gattCharacteristicValues.length()) {
        int comma = item.gattCharacteristicValues.indexOf(',', valueStart);
        String entry = comma < 0
          ? item.gattCharacteristicValues.substring(valueStart)
          : item.gattCharacteristicValues.substring(valueStart, comma);
        int separator = entry.indexOf('=');
        if (separator > 0) {
          characteristicValues[entry.substring(0, separator)] = entry.substring(separator + 1);
        }
        if (comma < 0) {
          break;
        }
        valueStart = comma + 1;
      }
    }
  }

  if (doc.overflowed() || doc["batch_id"].isNull() || observations.size() != batchSize) {
    if (batchSize > 1) {
      pendingBatchSize = max<size_t>(1, batchSize / 2);
    }
    Serial.printf(
      "[firmware] Upload JSON allocation failed or overflowed (items=%u, capacity=%u, free_heap=%u). Retrying a smaller frame.\n",
      static_cast<unsigned int>(batchSize),
      static_cast<unsigned int>(documentCapacity),
      static_cast<unsigned int>(ESP.getFreeHeap())
    );
    return false;
  }

  String response;
  bool ok = httpRequestJson("POST", String("/api/scanners/") + SCANNER_ID + "/observations/batch", doc, response);
  if (ok) {
    popObservations(batchSize);
    pendingBatchId = "";
    pendingBatchSize = 0;
  }
  return ok;
}

class ScanCallbacks : public NimBLEAdvertisedDeviceCallbacks {
  void onResult(NimBLEAdvertisedDevice *device) override {
    int rssi = device->getRSSI();
    if (rssi < scannerConfig.rssiMin) {
      return;
    }

    Observation observation;
    observation.observationId = makeId("obs");
    observation.observedAt = isoNow();
    observation.timeSource = timeSource();
    observation.bootId = bootId;
    observation.monotonicMs = millis();
    observation.scanCycle = scanCycle;
    observation.clockSyncAgeMs = clockSyncAgeMs();
    observation.address = String(device->getAddress().toString().c_str());
    switch (device->getAddressType()) {
      case 0:
        observation.addressType = "public";
        break;
      case 1:
        observation.addressType = "random";
        break;
      case 2:
        observation.addressType = "public_identity";
        break;
      case 3:
        observation.addressType = "random_identity";
        break;
      default:
        observation.addressType = "unknown";
        break;
    }
    observation.rssi = rssi;
    observation.advertisedName = device->haveName() ? String(device->getName().c_str()) : "";
    observation.hasTxPower = device->haveTXPower();
    observation.txPower = observation.hasTxPower ? device->getTXPower() : 0;
    uint8_t advertisingType = device->getAdvType();
    switch (advertisingType) {
      case 0:
        observation.advertisingType = "adv_ind";
        break;
      case 1:
        observation.advertisingType = "adv_direct_ind_high";
        break;
      case 2:
        observation.advertisingType = "adv_scan_ind";
        break;
      case 3:
        observation.advertisingType = "adv_nonconn_ind";
        break;
      case 4:
        observation.advertisingType = "adv_direct_ind_low";
        break;
      default:
        observation.advertisingType = "unknown";
        break;
    }
    // NimBLE-Arduino 1.x can interpret legacy advertising enum bits as
    // extended-advertising flags. Legacy ADV_IND and directed ADV are the
    // connection-capable report types; SCAN_IND/NONCONN_IND are not.
    observation.connectable = advertisingType == 0 || advertisingType == 1;
    observation.packetLength = device->getPayloadLength();
    observation.advertisingPacketLength = min<size_t>(device->getAdvLength(), observation.packetLength);
    observation.scanResponsePacketLength = observation.packetLength - observation.advertisingPacketLength;
    const uint8_t *payload = device->getPayload();
    observation.rawAdvertisingPayloadHex = bytesToHex(payload, observation.advertisingPacketLength);
    if (observation.scanResponsePacketLength > 0) {
      observation.rawScanResponsePayloadHex = bytesToHex(
        payload + observation.advertisingPacketLength,
        observation.scanResponsePacketLength
      );
    }

    if (device->haveManufacturerData()) {
      observation.manufacturerDataHex = stringToHex(device->getManufacturerData());
    }

    String serviceList;
    for (int i = 0; i < device->getServiceUUIDCount(); i++) {
      if (serviceList.length() > 0) {
        serviceList += ",";
      }
      serviceList += String(device->getServiceUUID(i).toString().c_str());
    }
    observation.serviceUuids = serviceList;
    pushObservation(observation);
    if (observation.connectable) {
      queueEnrichmentTarget(
        observation.address,
        device->getAddressType(),
        observation.rssi,
        observation.advertisedName.length() > 0
      );
    }
  }
};

void setupBle() {
  NimBLEDevice::init("");
  NimBLEScan *scan = NimBLEDevice::getScan();
  scan->setAdvertisedDeviceCallbacks(new ScanCallbacks(), false);
  scan->setActiveScan(true);
  scan->setInterval(100);
  scan->setWindow(90);
}

void runScan() {
  NimBLEScan *scan = NimBLEDevice::getScan();
  scanCycle++;
  scan->start(SCAN_DURATION_SECONDS, false);
  scan->clearResults();
  enrichNextTarget();
}

void setup() {
  Serial.begin(115200);
  uint64_t hardwareId = ESP.getEfuseMac();
  bootId = String("boot-") + String(static_cast<uint32_t>(hardwareId >> 32), HEX)
    + String(static_cast<uint32_t>(hardwareId), HEX) + "-" + String(esp_random(), HEX);
  setupBle();
  fetchConfig();
  sendHeartbeat();
}

void loop() {
  pollSerialControl();
  uint32_t now = millis();

  if (now - lastConfigAt >= CONFIG_REFRESH_INTERVAL_MS) {
    lastConfigAt = now;
    fetchConfig();
  }

  if (now - lastScanAt >= scannerConfig.scanIntervalMs) {
    lastScanAt = now;
    runScan();
  }

  if (now - lastUploadAt >= scannerConfig.uploadIntervalMs) {
    lastUploadAt = now;
    uploadBatch();
  }

  if (now - lastHeartbeatAt >= HEARTBEAT_INTERVAL_MS) {
    lastHeartbeatAt = now;
    sendHeartbeat();
  }

  delay(50);
}
