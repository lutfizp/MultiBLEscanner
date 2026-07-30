#include <Arduino.h>
#include <ArduinoJson.h>
#include <NimBLEDevice.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

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

struct TrackingTarget {
  String observedIdentityId;
  String address;
  String addressType;
};

struct TrackingConfig {
  bool active = false;
  String sessionId;
  String mode = "fixed";
  uint32_t sampleIntervalMs = TRACKING_SAMPLE_INTERVAL_MS;
  uint32_t uploadIntervalMs = TRACKING_UPLOAD_INTERVAL_MS;
  size_t targetCount = 0;
  TrackingTarget targets[TRACKING_TARGET_LIMIT];
};

struct TrackingSample {
  uint64_t observedEpochMs = 0;
  uint32_t monotonicMs = 0;
  uint32_t sequence = 0;
  int rssi = -127;
  uint8_t targetIndex = 0;
};

struct TrackingNormalDeviceSlot {
  String address;
  uint32_t cycle = 0;
  size_t bestPayloadLength = 0;
  bool hasName = false;
};

struct GattWorkerContext {
  EnrichmentTarget target;
  String sourceObservationId;
  Observation result;
  NimBLEClient *client = nullptr;
  uint32_t startedAt = 0;
  bool done = false;
  bool abandoned = false;
};

struct DirectGattReadContext {
  TaskHandle_t waitingTask = nullptr;
  uint8_t value[GATT_VALUE_MAX_BYTES];
  size_t length = 0;
  int status = BLE_HS_EUNKNOWN;
  bool longRead = true;
  bool truncated = false;
};

class ScanCallbacks;

ScannerConfig scannerConfig;
TrackingConfig trackingConfig;
Observation observationBuffer[OBSERVATION_BUFFER_SIZE];
Observation pendingObservationBuffer[MAX_SERIAL_FRAME_OBSERVATIONS];
size_t bufferHead = 0;
size_t bufferTail = 0;
size_t bufferCount = 0;
uint32_t droppedObservations = 0;
uint32_t observationCounter = 0;
uint32_t lastScanAt = 0;
uint32_t scanCycle = 0;
uint32_t batchSequence = 0;
uint32_t lastClockSyncAt = 0;
int64_t clockOffsetMs = 0;
bool clockSynchronized = false;
String bootId;
bool bridgeResponseReady = false;
int bridgeResponseStatus = 0;
String pendingConfigPayload;
String pendingTimeSyncValue;
bool pendingConfigReady = false;
bool pendingTimeSyncReady = false;
String pendingBatchId;
uint32_t pendingBatchSequence = 0;
size_t pendingBatchSize = 0;
size_t pendingObservationCount = 0;
EnrichmentTarget enrichmentQueue[GATT_ENRICHMENT_QUEUE_SIZE];
size_t enrichmentQueueCount = 0;
EnrichmentCacheEntry enrichmentCache[GATT_ENRICHMENT_CACHE_SIZE];
size_t enrichmentCacheCount = 0;
size_t enrichmentCacheCursor = 0;
TrackingSample trackingSampleBuffer[TRACKING_SAMPLE_BUFFER_SIZE];
size_t trackingBufferHead = 0;
size_t trackingBufferTail = 0;
size_t trackingBufferCount = 0;
uint32_t droppedTrackingSamples = 0;
uint32_t pendingDroppedTrackingSamples = 0;
uint32_t trackingSequence = 0;
uint32_t lastTrackingUploadAt = 0;
uint32_t lastTrackingCycleAt = 0;
uint32_t lastTrackingSampleAt[TRACKING_TARGET_LIMIT] = {0};
TrackingSample pendingTrackingSampleBuffer[MAX_TRACKING_SERIAL_FRAME_SAMPLES];
String pendingTrackingBatchId;
size_t pendingTrackingBatchSize = 0;
uint32_t pendingTrackingConfigGeneration = 0;
bool trackingConfigChanged = false;
bool trackingScanActive = false;
uint32_t trackingConfigGeneration = 0;
TrackingNormalDeviceSlot trackingNormalSlots[TRACKING_NORMAL_DEVICE_SLOTS];
size_t trackingNormalSlotCursor = 0;
ScanCallbacks *scanCallbacks = nullptr;
SemaphoreHandle_t observationBufferMutex = nullptr;
SemaphoreHandle_t trackingConfigMutex = nullptr;
SemaphoreHandle_t serialControlMutex = nullptr;
TaskHandle_t transportTaskHandle = nullptr;
volatile bool normalUploadReady = false;
uint32_t transportRequestSequence = 0;
uint32_t transportTimeoutCount = 0;
uint32_t transportFailureCount = 0;
uint32_t transportLastDurationMs = 0;
int transportLastStatus = 0;
String transportLastPath;
portMUX_TYPE trackingBufferMux = portMUX_INITIALIZER_UNLOCKED;
portMUX_TYPE idMux = portMUX_INITIALIZER_UNLOCKED;
GattWorkerContext *gattWorkerContext = nullptr;
SemaphoreHandle_t gattWorkerMutex = nullptr;

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

String observedText(const std::string &value) {
  const uint8_t *bytes = reinterpret_cast<const uint8_t *>(value.data());
  if (value.empty() || !isValidUtf8(bytes, value.length())) {
    return String("");
  }
  return String(value.c_str());
}

String gattTextValue(const uint8_t *value, size_t valueLength) {
  size_t length = valueLength;
  while (length > 0 && value[length - 1] == 0) {
    length--;
  }
  if (length == 0 || !isValidUtf8(value, length)) {
    return String("");
  }
  String output;
  output.reserve(length);
  for (size_t index = 0; index < length; index++) {
    output += static_cast<char>(value[index]);
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

String isoFromEpochMs(uint64_t epochMs) {
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

String isoNow() {
  return isoFromEpochMs(epochNowMs());
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

void clearTrackingSamples(bool countAsDropped) {
  portENTER_CRITICAL(&trackingBufferMux);
  if (countAsDropped) {
    droppedTrackingSamples += trackingBufferCount;
  }
  trackingBufferHead = 0;
  trackingBufferTail = 0;
  trackingBufferCount = 0;
  portEXIT_CRITICAL(&trackingBufferMux);
}

bool sameTrackingTargets(const TrackingConfig &left, const TrackingConfig &right) {
  if (left.active != right.active ||
      left.sessionId != right.sessionId ||
      left.targetCount != right.targetCount) {
    return false;
  }
  for (size_t index = 0; index < left.targetCount; index++) {
    if (left.targets[index].observedIdentityId != right.targets[index].observedIdentityId ||
        left.targets[index].address != right.targets[index].address ||
        left.targets[index].addressType != right.targets[index].addressType) {
      return false;
    }
  }
  return true;
}

void applyConfig(const String &payload) {
  DynamicJsonDocument doc(4096);
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
    // Server-side visibility policy must not remove factual weak RF captures.
    // Keep only the controller's practical receive floor in firmware.
    scannerConfig.rssiMin = min(rssiMin, DEFAULT_RSSI_MIN_DBM);
  }
  scannerConfig.configVersion = version;

  TrackingConfig nextTracking;
  JsonVariant focus = doc["tracking_focus"];
  if (!focus.isNull() && focus.is<JsonObject>()) {
    String sessionId = focus["session_id"] | "";
    JsonArray targets = focus["target_identities"].as<JsonArray>();
    if (sessionId.length() > 0 && !targets.isNull()) {
      nextTracking.active = true;
      nextTracking.sessionId = sessionId;
      nextTracking.mode = String(focus["mode"] | "fixed");
      uint32_t sampleIntervalMs = focus["sample_interval_ms"] | TRACKING_SAMPLE_INTERVAL_MS;
      uint32_t trackingUploadIntervalMs = focus["upload_interval_ms"] | TRACKING_UPLOAD_INTERVAL_MS;
      nextTracking.sampleIntervalMs = constrain(sampleIntervalMs, 100UL, 2000UL);
      nextTracking.uploadIntervalMs = constrain(trackingUploadIntervalMs, 200UL, 5000UL);
      for (JsonObject target : targets) {
        if (nextTracking.targetCount >= TRACKING_TARGET_LIMIT) {
          break;
        }
        String address = target["address"] | "";
        String addressType = target["address_type"] | "";
        String identityId = target["observed_identity_id"] | "";
        address.toLowerCase();
        address.replace("-", ":");
        addressType.toLowerCase();
        if (address.length() == 0 || addressType.length() == 0 || identityId.length() == 0) {
          continue;
        }
        TrackingTarget &destination = nextTracking.targets[nextTracking.targetCount++];
        destination.observedIdentityId = identityId;
        destination.address = address;
        destination.addressType = addressType;
      }
      nextTracking.active = nextTracking.targetCount > 0;
    }
  }

  xSemaphoreTake(trackingConfigMutex, portMAX_DELAY);
  if (!sameTrackingTargets(trackingConfig, nextTracking)) {
    clearTrackingSamples(false);
    droppedTrackingSamples = 0;
    trackingConfig = nextTracking;
    trackingConfigGeneration++;
    trackingSequence = 0;
    memset(lastTrackingSampleAt, 0, sizeof(lastTrackingSampleAt));
    trackingConfigChanged = true;
  } else {
    trackingConfig.sampleIntervalMs = nextTracking.sampleIntervalMs;
    trackingConfig.uploadIntervalMs = nextTracking.uploadIntervalMs;
    trackingConfig.mode = nextTracking.mode;
  }
  xSemaphoreGive(trackingConfigMutex);
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
        xSemaphoreTake(serialControlMutex, portMAX_DELAY);
        pendingTimeSyncValue = command.substring(strlen(TIME_SYNC_PREFIX));
        pendingTimeSyncReady = true;
        xSemaphoreGive(serialControlMutex);
      } else if (command.startsWith(BRIDGE_CONFIG_PREFIX)) {
        xSemaphoreTake(serialControlMutex, portMAX_DELAY);
        pendingConfigPayload = command.substring(strlen(BRIDGE_CONFIG_PREFIX));
        pendingConfigReady = true;
        xSemaphoreGive(serialControlMutex);
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

void applyPendingSerialControl() {
  String timeSyncValue;
  String configPayload;
  bool hasTimeSync = false;
  bool hasConfig = false;
  xSemaphoreTake(serialControlMutex, portMAX_DELAY);
  if (pendingTimeSyncReady) {
    timeSyncValue = pendingTimeSyncValue;
    pendingTimeSyncValue = "";
    pendingTimeSyncReady = false;
    hasTimeSync = true;
  }
  if (pendingConfigReady) {
    configPayload = pendingConfigPayload;
    pendingConfigPayload = "";
    pendingConfigReady = false;
    hasConfig = true;
  }
  xSemaphoreGive(serialControlMutex);
  if (hasTimeSync) {
    applyTimeSync(timeSyncValue);
  }
  if (hasConfig) {
    applyConfig(configPayload);
  }
}

String makeId(const char *prefix) {
  portENTER_CRITICAL(&idMux);
  observationCounter++;
  uint32_t sequence = observationCounter;
  portEXIT_CRITICAL(&idMux);
  return String(prefix) + "-" + SCANNER_ID + "-" + String(millis()) + "-" + String(sequence);
}

void pushObservation(const Observation &observation) {
  xSemaphoreTake(observationBufferMutex, portMAX_DELAY);
  if (bufferCount == OBSERVATION_BUFFER_SIZE) {
    // Preserve queued retry order. New RF evidence is counted as dropped when
    // the bounded queue is full instead of invalidating an in-flight batch.
    droppedObservations++;
    xSemaphoreGive(observationBufferMutex);
    return;
  }
  observationBuffer[bufferHead] = observation;
  bufferHead = (bufferHead + 1) % OBSERVATION_BUFFER_SIZE;
  bufferCount++;
  xSemaphoreGive(observationBufferMutex);
}

void popObservationsLocked(size_t count) {
  size_t removeCount = min(count, bufferCount);
  bufferTail = (bufferTail + removeCount) % OBSERVATION_BUFFER_SIZE;
  bufferCount -= removeCount;
}

const char *addressTypeName(uint8_t addressType) {
  switch (addressType) {
    case 0:
      return "public";
    case 1:
      return "random";
    case 2:
      return "public_identity";
    case 3:
      return "random_identity";
    default:
      return "unknown";
  }
}

bool trackingIsActive() {
  xSemaphoreTake(trackingConfigMutex, portMAX_DELAY);
  bool active = trackingConfig.active;
  xSemaphoreGive(trackingConfigMutex);
  return active;
}

TrackingConfig snapshotTrackingConfig(uint32_t *generation = nullptr) {
  xSemaphoreTake(trackingConfigMutex, portMAX_DELAY);
  TrackingConfig snapshot = trackingConfig;
  if (generation != nullptr) {
    *generation = trackingConfigGeneration;
  }
  xSemaphoreGive(trackingConfigMutex);
  return snapshot;
}

void captureTrackingSample(const String &address, const String &addressType, int rssi) {
  uint64_t capturedAt = epochNowMs();
  if (capturedAt == 0 || rssi < -110) {
    return;
  }
  xSemaphoreTake(trackingConfigMutex, portMAX_DELAY);
  if (!trackingConfig.active) {
    xSemaphoreGive(trackingConfigMutex);
    return;
  }
  int targetIndex = -1;
  for (size_t index = 0; index < trackingConfig.targetCount; index++) {
    if (trackingConfig.targets[index].address == address &&
        trackingConfig.targets[index].addressType == addressType) {
      targetIndex = static_cast<int>(index);
      break;
    }
  }
  if (targetIndex < 0) {
    xSemaphoreGive(trackingConfigMutex);
    return;
  }
  uint32_t now = millis();
  if (lastTrackingSampleAt[targetIndex] != 0 &&
      now - lastTrackingSampleAt[targetIndex] < trackingConfig.sampleIntervalMs) {
    xSemaphoreGive(trackingConfigMutex);
    return;
  }
  lastTrackingSampleAt[targetIndex] = now;

  TrackingSample sample;
  sample.observedEpochMs = capturedAt;
  sample.monotonicMs = now;
  sample.sequence = ++trackingSequence;
  sample.rssi = rssi;
  sample.targetIndex = static_cast<uint8_t>(targetIndex);

  portENTER_CRITICAL(&trackingBufferMux);
  if (trackingBufferCount == TRACKING_SAMPLE_BUFFER_SIZE) {
    trackingBufferTail = (trackingBufferTail + 1) % TRACKING_SAMPLE_BUFFER_SIZE;
    trackingBufferCount--;
    droppedTrackingSamples++;
  }
  trackingSampleBuffer[trackingBufferHead] = sample;
  trackingBufferHead = (trackingBufferHead + 1) % TRACKING_SAMPLE_BUFFER_SIZE;
  trackingBufferCount++;
  portEXIT_CRITICAL(&trackingBufferMux);
  xSemaphoreGive(trackingConfigMutex);
}

bool allowTrackingNormalObservation(
  const String &address,
  bool hasName,
  size_t payloadLength
) {
  if (!trackingIsActive()) {
    return true;
  }
  size_t freeIndex = TRACKING_NORMAL_DEVICE_SLOTS;
  for (size_t index = 0; index < TRACKING_NORMAL_DEVICE_SLOTS; index++) {
    TrackingNormalDeviceSlot &slot = trackingNormalSlots[index];
    if (slot.address == address) {
      if (slot.cycle != scanCycle) {
        slot.cycle = scanCycle;
        slot.bestPayloadLength = payloadLength;
        slot.hasName = hasName;
        return true;
      }
      bool richer = (hasName && !slot.hasName) || payloadLength > slot.bestPayloadLength;
      slot.hasName = slot.hasName || hasName;
      slot.bestPayloadLength = max(slot.bestPayloadLength, payloadLength);
      return richer;
    }
    if (freeIndex == TRACKING_NORMAL_DEVICE_SLOTS && slot.address.length() == 0) {
      freeIndex = index;
    }
  }
  size_t targetIndex = freeIndex < TRACKING_NORMAL_DEVICE_SLOTS
    ? freeIndex
    : trackingNormalSlotCursor++ % TRACKING_NORMAL_DEVICE_SLOTS;
  trackingNormalSlots[targetIndex].address = address;
  trackingNormalSlots[targetIndex].cycle = scanCycle;
  trackingNormalSlots[targetIndex].bestPayloadLength = payloadLength;
  trackingNormalSlots[targetIndex].hasName = hasName;
  return true;
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

String latestBufferedObservationId(const String &address) {
  String observationId;
  xSemaphoreTake(observationBufferMutex, portMAX_DELAY);
  for (size_t offset = 0; offset < bufferCount; offset++) {
    size_t index = (bufferHead + OBSERVATION_BUFFER_SIZE - 1 - offset) % OBSERVATION_BUFFER_SIZE;
    if (observationBuffer[index].address == address) {
      observationId = observationBuffer[index].observationId;
      break;
    }
  }
  xSemaphoreGive(observationBufferMutex);
  return observationId;
}

Observation *bufferedObservationByIdLocked(const String &observationId) {
  for (size_t offset = 0; offset < bufferCount; offset++) {
    size_t index = (bufferTail + offset) % OBSERVATION_BUFFER_SIZE;
    if (observationBuffer[index].observationId == observationId) {
      return &observationBuffer[index];
    }
  }
  return nullptr;
}

void markBufferedObservationGattFailure(
  const String &observationId,
  const String &status,
  const String &errorCode,
  uint32_t durationMs
) {
  xSemaphoreTake(observationBufferMutex, portMAX_DELAY);
  Observation *source = bufferedObservationByIdLocked(observationId);
  if (source != nullptr) {
    source->gattAttempted = true;
    source->gattStatus = status;
    source->gattErrorCode = errorCode;
    source->gattAttemptDurationMs = durationMs;
  }
  xSemaphoreGive(observationBufferMutex);
}

void copyGattResult(Observation &destination, const Observation &source) {
  destination.gattAttempted = source.gattAttempted;
  destination.gattStatus = source.gattStatus;
  destination.gattErrorCode = source.gattErrorCode;
  destination.gattDeviceName = source.gattDeviceName;
  destination.gattManufacturerName = source.gattManufacturerName;
  destination.gattModelNumber = source.gattModelNumber;
  destination.gattSerialNumber = source.gattSerialNumber;
  destination.gattFirmwareRevision = source.gattFirmwareRevision;
  destination.gattHardwareRevision = source.gattHardwareRevision;
  destination.gattSoftwareRevision = source.gattSoftwareRevision;
  destination.gattSystemIdHex = source.gattSystemIdHex;
  destination.gattPnpIdHex = source.gattPnpIdHex;
  destination.gattDiscoveredServices = source.gattDiscoveredServices;
  destination.gattCharacteristicValues = source.gattCharacteristicValues;
  destination.gattAttemptDurationMs = source.gattAttemptDurationMs;
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

bool isGattSecurityError(int status) {
  return status == BLE_HS_ATT_ERR(BLE_ATT_ERR_INSUFFICIENT_AUTHEN)
    || status == BLE_HS_ATT_ERR(BLE_ATT_ERR_INSUFFICIENT_AUTHOR)
    || status == BLE_HS_ATT_ERR(BLE_ATT_ERR_INSUFFICIENT_ENC)
    || status == BLE_HS_ATT_ERR(BLE_ATT_ERR_INSUFFICIENT_KEY_SZ);
}

int directGattReadCallback(
  uint16_t,
  const ble_gatt_error *error,
  ble_gatt_attr *attribute,
  void *argument
) {
  DirectGattReadContext *context = static_cast<DirectGattReadContext *>(argument);
  int status = error == nullptr ? BLE_HS_EUNKNOWN : error->status;
  if (status == 0 && attribute != nullptr && attribute->om != nullptr) {
    size_t chunkLength = OS_MBUF_PKTLEN(attribute->om);
    size_t offset = attribute->offset;
    size_t copyLength = 0;
    if (offset < GATT_VALUE_MAX_BYTES) {
      copyLength = chunkLength;
      size_t available = GATT_VALUE_MAX_BYTES - offset;
      if (copyLength > available) {
        copyLength = available;
        context->truncated = true;
      }
      if (
        copyLength > 0
        && attribute->om->om_data == nullptr
      ) {
        context->status = BLE_HS_EUNKNOWN;
        xTaskNotifyGive(context->waitingTask);
        return BLE_HS_EUNKNOWN;
      }
      if (copyLength > 0) {
        memcpy(context->value + offset, attribute->om->om_data, copyLength);
      }
      size_t observedLength = offset + copyLength;
      if (observedLength > context->length) {
        context->length = observedLength;
      }
    } else if (chunkLength > 0) {
      context->truncated = true;
    }

    if (context->longRead) {
      return 0;
    }
  }

  context->status = status;
  xTaskNotifyGive(context->waitingTask);
  return 0;
}

bool runDirectGattRead(
  NimBLEClient *client,
  uint16_t characteristicHandle,
  DirectGattReadContext &context
) {
  context.waitingTask = xTaskGetCurrentTaskHandle();
  while (ulTaskNotifyTake(pdTRUE, 0) > 0) {
  }

  int startStatus = ble_gattc_read_long(
    client->getConnHandle(),
    characteristicHandle,
    0,
    directGattReadCallback,
    &context
  );
  if (startStatus != 0) {
    context.status = startStatus;
    return false;
  }
  ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

  int attributeNotLong = BLE_HS_ATT_ERR(BLE_ATT_ERR_ATTR_NOT_LONG);
  if (context.status == attributeNotLong && context.length == 0) {
    context.longRead = false;
    context.status = BLE_HS_EUNKNOWN;
    context.truncated = false;
    while (ulTaskNotifyTake(pdTRUE, 0) > 0) {
    }
    startStatus = ble_gattc_read(
      client->getConnHandle(),
      characteristicHandle,
      directGattReadCallback,
      &context
    );
    if (startStatus != 0) {
      context.status = startStatus;
      return false;
    }
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
  }

  bool completedNormally = context.status == 0
    || context.status == BLE_HS_EDONE
    || context.status == attributeNotLong;
  return completedNormally && context.length > 0;
}

NimBLERemoteService *findGattService(
  const std::vector<NimBLERemoteService *> &services,
  const char *uuid
) {
  NimBLEUUID targetUuid(uuid);
  for (NimBLERemoteService *service : services) {
    if (service != nullptr && service->getUUID() == targetUuid) {
      return service;
    }
  }
  return nullptr;
}

NimBLERemoteCharacteristic *findGattCharacteristic(
  NimBLERemoteService *service,
  const char *uuid
) {
  if (service == nullptr) {
    return nullptr;
  }
  NimBLEUUID targetUuid(uuid);
  const std::vector<NimBLERemoteCharacteristic *> &characteristics =
    service->getCharacteristics(false);
  for (NimBLERemoteCharacteristic *characteristic : characteristics) {
    if (characteristic != nullptr && characteristic->getUUID() == targetUuid) {
      return characteristic;
    }
  }
  return nullptr;
}

bool readGattCharacteristic(
  NimBLEClient *client,
  NimBLERemoteService *service,
  const char *characteristicUuid,
  Observation &observation,
  String *textValue,
  String *binaryHexValue,
  bool &securityRequired,
  bool &valueTruncated,
  int &lastReadError
) {
  NimBLERemoteCharacteristic *characteristic =
    findGattCharacteristic(service, characteristicUuid);
  if (characteristic == nullptr || !characteristic->canRead()) {
    return false;
  }

  DirectGattReadContext directRead;
  if (!runDirectGattRead(client, characteristic->getHandle(), directRead)) {
    lastReadError = directRead.status;
    securityRequired = securityRequired || isGattSecurityError(directRead.status);
    return false;
  }
  valueTruncated = valueTruncated || directRead.truncated;
  String rawHex = bytesToHex(directRead.value, directRead.length);
  appendGattCharacteristic(observation, characteristicUuid, rawHex);
  if (textValue != nullptr) {
    *textValue = gattTextValue(directRead.value, directRead.length);
  }
  if (binaryHexValue != nullptr) {
    *binaryHexValue = rawHex;
  }
  return true;
}

bool gattContextAbandoned(GattWorkerContext *context) {
  if (gattWorkerMutex == nullptr) {
    return true;
  }
  xSemaphoreTake(gattWorkerMutex, portMAX_DELAY);
  bool abandoned = context->abandoned;
  xSemaphoreGive(gattWorkerMutex);
  return abandoned;
}

bool attachGattClient(GattWorkerContext *context, NimBLEClient *client) {
  xSemaphoreTake(gattWorkerMutex, portMAX_DELAY);
  bool attached = !context->abandoned;
  if (attached) {
    context->client = client;
  }
  xSemaphoreGive(gattWorkerMutex);
  return attached;
}

void releaseGattClient(GattWorkerContext *context, NimBLEClient *client) {
  xSemaphoreTake(gattWorkerMutex, portMAX_DELAY);
  if (context->client == client) {
    context->client = nullptr;
  }
  xSemaphoreGive(gattWorkerMutex);
  NimBLEDevice::deleteClient(client);
}

void performGattEnrichment(GattWorkerContext *context) {
  Observation &observation = context->result;
  observation.gattAttempted = true;
  NimBLEClient *client = NimBLEDevice::createClient();
  if (client == nullptr) {
    observation.gattStatus = "connection_failed";
    observation.gattErrorCode = "client_allocation_failed";
    observation.gattAttemptDurationMs = millis() - context->startedAt;
    return;
  }
  if (!attachGattClient(context, client)) {
    observation.gattStatus = "cancelled";
    observation.gattErrorCode = "worker_abandoned_before_connect";
    observation.gattAttemptDurationMs = millis() - context->startedAt;
    NimBLEDevice::deleteClient(client);
    return;
  }

  NimBLEClient::Config clientConfig = client->getConfig();
  clientConfig.connectFailRetries = 0;
  client->setConfig(clientConfig);
  client->setConnectTimeout(GATT_CONNECT_TIMEOUT_MS);
  client->setConnectionParams(24, 40, 0, 60);
  NimBLEAddress peerAddress(
    std::string(context->target.address.c_str()),
    context->target.addressType
  );
  if (!client->connect(peerAddress)) {
    if (gattContextAbandoned(context)) {
      observation.gattStatus = "cancelled";
      observation.gattErrorCode = "worker_abandoned_during_connect";
    } else {
      observation.gattStatus = "connection_failed";
      observation.gattErrorCode = String("nimble_") + String(client->getLastError());
    }
    observation.gattAttemptDurationMs = millis() - context->startedAt;
    releaseGattClient(context, client);
    return;
  }

  const std::vector<NimBLERemoteService *> &services = client->getServices(true);
  for (NimBLERemoteService *service : services) {
    if (service != nullptr) {
      appendCsvValue(
        observation.gattDiscoveredServices,
        String(service->getUUID().toString().c_str())
      );
    }
  }

  NimBLERemoteService *gapService = findGattService(services, "1800");
  NimBLERemoteService *deviceInformationService = findGattService(services, "180a");
  if (gapService != nullptr && !gattContextAbandoned(context)) {
    gapService->getCharacteristics(true);
  }
  if (deviceInformationService != nullptr && !gattContextAbandoned(context)) {
    deviceInformationService->getCharacteristics(true);
  }

  size_t readCount = 0;
  bool securityRequired = false;
  bool valueTruncated = false;
  int lastReadError = 0;
  String ignoredText;
  auto readIdentityValue = [&](
    NimBLERemoteService *service,
    const char *characteristicUuid,
    String *textValue,
    String *binaryHexValue
  ) {
    if (gattContextAbandoned(context)) {
      return false;
    }
    return readGattCharacteristic(
      client,
      service,
      characteristicUuid,
      observation,
      textValue,
      binaryHexValue,
      securityRequired,
      valueTruncated,
      lastReadError
    );
  };

  readCount += readIdentityValue(gapService, "2a00", &observation.gattDeviceName, nullptr);
  readCount += readIdentityValue(gapService, "2a01", &ignoredText, nullptr);
  readCount += readIdentityValue(
    deviceInformationService,
    "2a23",
    nullptr,
    &observation.gattSystemIdHex
  );
  readCount += readIdentityValue(
    deviceInformationService,
    "2a24",
    &observation.gattModelNumber,
    nullptr
  );
  readCount += readIdentityValue(
    deviceInformationService,
    "2a25",
    &observation.gattSerialNumber,
    nullptr
  );
  readCount += readIdentityValue(
    deviceInformationService,
    "2a26",
    &observation.gattFirmwareRevision,
    nullptr
  );
  readCount += readIdentityValue(
    deviceInformationService,
    "2a27",
    &observation.gattHardwareRevision,
    nullptr
  );
  readCount += readIdentityValue(
    deviceInformationService,
    "2a28",
    &observation.gattSoftwareRevision,
    nullptr
  );
  readCount += readIdentityValue(
    deviceInformationService,
    "2a29",
    &observation.gattManufacturerName,
    nullptr
  );
  readCount += readIdentityValue(deviceInformationService, "2a2a", &ignoredText, nullptr);
  readCount += readIdentityValue(
    deviceInformationService,
    "2a50",
    nullptr,
    &observation.gattPnpIdHex
  );

  if (gattContextAbandoned(context)) {
    observation.gattStatus = "cancelled";
    observation.gattErrorCode = "worker_abandoned_during_gatt";
  } else if (services.empty()) {
    observation.gattStatus = "service_discovery_failed";
    observation.gattErrorCode = String("nimble_") + String(client->getLastError());
  } else if (readCount == 0) {
    observation.gattStatus = securityRequired ? "security_required" : "partial";
    observation.gattErrorCode = lastReadError == 0
      ? "no_readable_identity_characteristics"
      : String("nimble_") + String(lastReadError);
  } else if (securityRequired || lastReadError != 0 || valueTruncated) {
    observation.gattStatus = "partial";
    observation.gattErrorCode = valueTruncated
      ? "gatt_value_truncated"
      : String("nimble_") + String(lastReadError);
  } else {
    observation.gattStatus = "success";
  }
  observation.gattAttemptDurationMs = millis() - context->startedAt;
  releaseGattClient(context, client);
}

void gattWorkerTask(void *argument) {
  GattWorkerContext *context = static_cast<GattWorkerContext *>(argument);
  performGattEnrichment(context);
  xSemaphoreTake(gattWorkerMutex, portMAX_DELAY);
  context->done = true;
  xSemaphoreGive(gattWorkerMutex);
  vTaskDelete(nullptr);
}

void cancelGattClientLocked(GattWorkerContext *context) {
  if (context->client == nullptr) {
    return;
  }
  if (context->client->isConnected()) {
    context->client->disconnect();
  } else {
    context->client->cancelConnect();
  }
}

void markGattSource(
  GattWorkerContext *context,
  const String &status,
  const String &errorCode,
  uint32_t durationMs
) {
  markBufferedObservationGattFailure(
    context->sourceObservationId,
    status,
    errorCode,
    durationMs
  );
}

void processGattWorker() {
  if (gattWorkerMutex == nullptr) {
    return;
  }

  GattWorkerContext *context = nullptr;
  bool timedOut = false;
  bool done = false;
  bool abandoned = false;
  uint32_t durationMs = 0;
  xSemaphoreTake(gattWorkerMutex, portMAX_DELAY);
  context = gattWorkerContext;
  if (context != nullptr) {
    durationMs = millis() - context->startedAt;
    if (
      !context->done
      && !context->abandoned
      && durationMs >= GATT_OPERATION_TIMEOUT_MS
    ) {
      context->abandoned = true;
      timedOut = true;
      cancelGattClientLocked(context);
    }
    done = context->done;
    abandoned = context->abandoned;
    if (done) {
      gattWorkerContext = nullptr;
    }
  }
  xSemaphoreGive(gattWorkerMutex);

  if (context == nullptr) {
    return;
  }
  if (timedOut) {
    markGattSource(context, "operation_timeout", "gatt_operation_timeout", durationMs);
  }
  if (!done) {
    return;
  }
  if (!abandoned) {
    xSemaphoreTake(observationBufferMutex, portMAX_DELAY);
    Observation *source = bufferedObservationByIdLocked(context->sourceObservationId);
    if (source != nullptr) {
      copyGattResult(*source, context->result);
    }
    xSemaphoreGive(observationBufferMutex);
  }
  delete context;
}

void abandonGattWorker(const String &status, const String &errorCode) {
  if (gattWorkerMutex == nullptr) {
    return;
  }

  GattWorkerContext *context = nullptr;
  uint32_t durationMs = 0;
  bool changed = false;
  xSemaphoreTake(gattWorkerMutex, portMAX_DELAY);
  context = gattWorkerContext;
  if (context != nullptr && !context->done && !context->abandoned) {
    context->abandoned = true;
    durationMs = millis() - context->startedAt;
    cancelGattClientLocked(context);
    changed = true;
  }
  xSemaphoreGive(gattWorkerMutex);
  if (changed) {
    markGattSource(context, status, errorCode, durationMs);
  }
}

bool gattWorkerBlocksUpload() {
  if (gattWorkerMutex == nullptr) {
    return false;
  }
  xSemaphoreTake(gattWorkerMutex, portMAX_DELAY);
  bool blocked = gattWorkerContext != nullptr
    && !gattWorkerContext->done
    && !gattWorkerContext->abandoned;
  xSemaphoreGive(gattWorkerMutex);
  return blocked;
}

void enrichNextTarget() {
  if (gattWorkerMutex == nullptr) {
    return;
  }
  xSemaphoreTake(gattWorkerMutex, portMAX_DELAY);
  bool workerExists = gattWorkerContext != nullptr;
  xSemaphoreGive(gattWorkerMutex);
  if (workerExists) {
    return;
  }

  EnrichmentTarget target;
  if (!takeNextEnrichmentTarget(target)) {
    return;
  }
  String sourceObservationId = latestBufferedObservationId(target.address);
  if (sourceObservationId.length() == 0) {
    return;
  }
  rememberEnrichmentAttempt(target.address, target.addressType);

  GattWorkerContext *context = new GattWorkerContext();
  if (context == nullptr) {
    markBufferedObservationGattFailure(
      sourceObservationId,
      "connection_failed",
      "worker_context_allocation_failed",
      0
    );
    return;
  }
  context->target = target;
  context->sourceObservationId = sourceObservationId;
  context->startedAt = millis();

  xSemaphoreTake(gattWorkerMutex, portMAX_DELAY);
  gattWorkerContext = context;
  xSemaphoreGive(gattWorkerMutex);
  BaseType_t created = xTaskCreate(
    gattWorkerTask,
    "ble-gatt",
    GATT_WORKER_STACK_SIZE,
    context,
    1,
    nullptr
  );
  if (created != pdPASS) {
    xSemaphoreTake(gattWorkerMutex, portMAX_DELAY);
    if (gattWorkerContext == context) {
      gattWorkerContext = nullptr;
    }
    xSemaphoreGive(gattWorkerMutex);
    markBufferedObservationGattFailure(
      sourceObservationId,
      "connection_failed",
      "worker_task_allocation_failed",
      millis() - context->startedAt
    );
    delete context;
  }
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
  uint32_t startedAt = millis();
  transportRequestSequence++;
  transportLastPath = path;
  bridgeResponseReady = false;
  bridgeResponseStatus = 0;
  Serial.println("|||BRIDGE_START|||");
  Serial.println(method);
  Serial.println(path);
  Serial.println(body);
  Serial.println("|||BRIDGE_END|||");
  Serial.flush();
  bool ok = waitForBridgeResponse(responseBody);
  transportLastDurationMs = millis() - startedAt;
  transportLastStatus = bridgeResponseReady ? bridgeResponseStatus : 0;
  if (!bridgeResponseReady) {
    transportTimeoutCount++;
  } else if (!ok) {
    transportFailureCount++;
  }
  return ok;
}

bool httpRequestJson(const char *method, const String &path, JsonDocument &document, String &responseBody) {
  uint32_t startedAt = millis();
  transportRequestSequence++;
  transportLastPath = path;
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
  bool ok = waitForBridgeResponse(responseBody);
  transportLastDurationMs = millis() - startedAt;
  transportLastStatus = bridgeResponseReady ? bridgeResponseStatus : 0;
  if (!bridgeResponseReady) {
    transportTimeoutCount++;
  } else if (!ok) {
    transportFailureCount++;
  }
  return ok;
}

void fetchConfig() {
  String response;
  httpRequest("GET", String("/api/scanners/") + SCANNER_ID + "/config", "", response);
}

void sendHeartbeat() {
  TrackingConfig tracking = snapshotTrackingConfig();
  DynamicJsonDocument doc(2048);
  doc["message_id"] = makeId("hb");
  doc["scanner_time"] = isoNow();
  doc["uptime_seconds"] = millis() / 1000;
  doc["firmware_version"] = FIRMWARE_VERSION;
  doc["hardware_version"] = HARDWARE_VERSION;
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
  health["tracking_session_id"] = tracking.active ? tracking.sessionId : "";
  health["tracking_state"] = tracking.active
    ? (trackingScanActive ? "active" : "waiting")
    : "inactive";
  if (gattWorkerMutex == nullptr) {
    health["gatt_worker_state"] = "unavailable";
  } else {
    xSemaphoreTake(gattWorkerMutex, portMAX_DELAY);
    if (gattWorkerContext == nullptr) {
      health["gatt_worker_state"] = "idle";
      health["gatt_worker_age_ms"] = 0;
    } else {
      health["gatt_worker_state"] = gattWorkerContext->done
        ? "completed"
        : (gattWorkerContext->abandoned ? "cancelling" : "active");
      health["gatt_worker_age_ms"] = millis() - gattWorkerContext->startedAt;
    }
    xSemaphoreGive(gattWorkerMutex);
  }
  size_t pendingTrackingSamples = 0;
  uint32_t droppedFocusSamples = 0;
  portENTER_CRITICAL(&trackingBufferMux);
  pendingTrackingSamples = trackingBufferCount + pendingTrackingBatchSize;
  droppedFocusSamples = droppedTrackingSamples + pendingDroppedTrackingSamples;
  portEXIT_CRITICAL(&trackingBufferMux);
  health["pending_tracking_samples"] = pendingTrackingSamples;
  health["dropped_tracking_samples"] = droppedFocusSamples;
  size_t pendingObservations = pendingObservationCount;
  xSemaphoreTake(observationBufferMutex, portMAX_DELAY);
  pendingObservations += bufferCount;
  xSemaphoreGive(observationBufferMutex);
  doc["pending_observations"] = pendingObservations;
  doc["dropped_observations"] = droppedObservations;
  doc["buffer_usage"] = min<size_t>(
    100,
    (pendingObservations * 100) / (OBSERVATION_BUFFER_SIZE + MAX_SERIAL_FRAME_OBSERVATIONS)
  );
  health["pending_observations"] = pendingObservations;
  health["dropped_observations"] = droppedObservations;
  health["transport_request_sequence"] = transportRequestSequence;
  health["transport_last_path"] = transportLastPath;
  health["transport_last_status"] = transportLastStatus;
  health["transport_last_duration_ms"] = transportLastDurationMs;
  health["transport_timeout_count"] = transportTimeoutCount;
  health["transport_failure_count"] = transportFailureCount;

  String body;
  serializeJson(doc, body);
  String response;
  httpRequest("POST", String("/api/scanners/") + SCANNER_ID + "/heartbeat", body, response);
}

bool uploadBatch() {
  if (pendingBatchId.length() == 0) {
    if (pendingObservationCount == 0) {
      xSemaphoreTake(observationBufferMutex, portMAX_DELAY);
      pendingObservationCount = min<size_t>(
        min<size_t>(bufferCount, scannerConfig.maxBatchSize),
        MAX_SERIAL_FRAME_OBSERVATIONS
      );
      for (size_t index = 0; index < pendingObservationCount; index++) {
        pendingObservationBuffer[index] = observationBuffer[
          (bufferTail + index) % OBSERVATION_BUFFER_SIZE
        ];
      }
      popObservationsLocked(pendingObservationCount);
      xSemaphoreGive(observationBufferMutex);
    }
    if (pendingObservationCount == 0) {
      return true;
    }
    pendingBatchSize = min<size_t>(
      pendingObservationCount,
      min<size_t>(scannerConfig.maxBatchSize, MAX_SERIAL_FRAME_OBSERVATIONS)
    );
    pendingBatchId = makeId("batch");
    pendingBatchSequence = ++batchSequence;
  }
  size_t batchSize = pendingBatchSize;
  if (batchSize == 0) {
    pendingBatchId = "";
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
    const Observation &item = pendingObservationBuffer[i];
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
    size_t remaining = pendingObservationCount - batchSize;
    for (size_t index = 0; index < remaining; index++) {
      pendingObservationBuffer[index] = pendingObservationBuffer[index + batchSize];
    }
    pendingObservationCount = remaining;
    pendingBatchId = "";
    pendingBatchSize = 0;
  }
  return ok;
}

bool uploadTrackingBatch() {
  uint32_t configGeneration = 0;
  TrackingConfig tracking = snapshotTrackingConfig(&configGeneration);
  if (
    pendingTrackingBatchId.length() > 0
    && pendingTrackingBatchSize > 0
    && pendingTrackingConfigGeneration != configGeneration
  ) {
    pendingTrackingBatchId = "";
    pendingTrackingBatchSize = 0;
    pendingDroppedTrackingSamples = 0;
  }
  if (!tracking.active) {
    return true;
  }

  if (pendingTrackingBatchId.length() == 0) {
    portENTER_CRITICAL(&trackingBufferMux);
    pendingTrackingBatchSize = min<size_t>(
      trackingBufferCount,
      MAX_TRACKING_SERIAL_FRAME_SAMPLES
    );
    for (size_t index = 0; index < pendingTrackingBatchSize; index++) {
      pendingTrackingSampleBuffer[index] = trackingSampleBuffer[trackingBufferTail];
      trackingBufferTail = (trackingBufferTail + 1) % TRACKING_SAMPLE_BUFFER_SIZE;
      trackingBufferCount--;
    }
    pendingDroppedTrackingSamples = droppedTrackingSamples;
    droppedTrackingSamples = 0;
    portEXIT_CRITICAL(&trackingBufferMux);
    if (pendingTrackingBatchSize == 0) {
      return true;
    }
    pendingTrackingBatchId = makeId("focus-batch");
    pendingTrackingConfigGeneration = configGeneration;
  }

  DynamicJsonDocument doc(2048 + pendingTrackingBatchSize * 512);
  doc["batch_id"] = pendingTrackingBatchId;
  doc["session_id"] = tracking.sessionId;
  doc["dropped_samples"] = pendingDroppedTrackingSamples;
  JsonArray samples = doc.createNestedArray("samples");
  for (size_t index = 0; index < pendingTrackingBatchSize; index++) {
    const TrackingSample &item = pendingTrackingSampleBuffer[index];
    if (item.targetIndex >= tracking.targetCount) {
      continue;
    }
    const TrackingTarget &target = tracking.targets[item.targetIndex];
    JsonObject sample = samples.createNestedObject();
    sample["sample_id"] = String("focus-") + tracking.sessionId + "-" + String(item.sequence);
    sample["observed_at"] = isoFromEpochMs(item.observedEpochMs);
    sample["boot_id"] = bootId;
    sample["monotonic_ms"] = item.monotonicMs;
    sample["sequence"] = item.sequence;
    sample["address"] = target.address;
    sample["address_type"] = target.addressType;
    sample["rssi"] = item.rssi;
  }

  if (doc.overflowed() || samples.size() != pendingTrackingBatchSize) {
    return false;
  }

  String response;
  bool ok = httpRequestJson(
    "POST",
    String("/api/scanners/") + SCANNER_ID + "/tracking-samples/batch",
    doc,
    response
  );
  if (ok) {
    pendingTrackingBatchId = "";
    pendingTrackingBatchSize = 0;
    pendingDroppedTrackingSamples = 0;
  }
  return ok;
}

class ScanCallbacks : public NimBLEScanCallbacks {
  void onResult(const NimBLEAdvertisedDevice *device) override {
    int rssi = device->getRSSI();
    String address = String(device->getAddress().toString().c_str());
    address.toLowerCase();
    String addressType = String(addressTypeName(device->getAddressType()));
    captureTrackingSample(address, addressType, rssi);
    if (rssi < scannerConfig.rssiMin) {
      return;
    }
    String advertisedName = device->haveName()
      ? observedText(device->getName())
      : String("");
    bool hasName = advertisedName.length() > 0;
    const std::vector<uint8_t> &payload = device->getPayload();
    if (!allowTrackingNormalObservation(address, hasName, payload.size())) {
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
    observation.address = address;
    observation.addressType = addressType;
    observation.rssi = rssi;
    observation.advertisedName = advertisedName;
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
    observation.connectable = device->isConnectable();
    observation.packetLength = payload.size();
    observation.advertisingPacketLength = min<size_t>(device->getAdvLength(), observation.packetLength);
    observation.scanResponsePacketLength = observation.packetLength - observation.advertisingPacketLength;
    observation.rawAdvertisingPayloadHex = bytesToHex(payload.data(), observation.advertisingPacketLength);
    if (observation.scanResponsePacketLength > 0) {
      observation.rawScanResponsePayloadHex = bytesToHex(
        payload.data() + observation.advertisingPacketLength,
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
    if (observation.connectable && !trackingIsActive()) {
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
  scanCallbacks = new ScanCallbacks();
  scan->setScanCallbacks(scanCallbacks, false);
  scan->setActiveScan(true);
  scan->setInterval(100);
  scan->setWindow(90);
  scan->setMaxResults(0);
}

void runScan() {
  NimBLEScan *scan = NimBLEDevice::getScan();
  normalUploadReady = false;
  scanCycle++;
  scan->getResults(SCAN_DURATION_SECONDS * 1000UL, false);
  scan->clearResults();
  enrichNextTarget();
  normalUploadReady = true;
}

void startTrackingScan() {
  NimBLEScan *scan = NimBLEDevice::getScan();
  if (scan->isScanning()) {
    scan->stop();
  }
  scan->setScanCallbacks(scanCallbacks, true);
  scan->setActiveScan(true);
  scan->clearResults();
  scanCycle++;
  lastTrackingCycleAt = millis();
  trackingScanActive = scan->start(0, false, true);
  normalUploadReady = true;
}

void stopTrackingScan() {
  NimBLEScan *scan = NimBLEDevice::getScan();
  if (scan->isScanning()) {
    scan->stop();
  }
  scan->setScanCallbacks(scanCallbacks, false);
  scan->clearResults();
  trackingScanActive = false;
}

void applyTrackingModeChange() {
  if (!trackingConfigChanged) {
    return;
  }
  trackingConfigChanged = false;
  TrackingConfig tracking = snapshotTrackingConfig();
  if (tracking.active) {
    abandonGattWorker("cancelled", "tracking_focus_started");
    if (!trackingScanActive) {
      startTrackingScan();
    }
    lastTrackingUploadAt = millis();
  } else {
    stopTrackingScan();
    normalUploadReady = false;
    lastScanAt = millis() - scannerConfig.scanIntervalMs;
  }
}

void transportTask(void *) {
  uint32_t lastUploadAt = 0;
  uint32_t lastHeartbeatAt = 0;
  uint32_t lastConfigAt = 0;

  fetchConfig();
  lastConfigAt = millis();
  sendHeartbeat();
  lastHeartbeatAt = millis();

  while (true) {
    pollSerialControl();
    TrackingConfig tracking = snapshotTrackingConfig();
    uint32_t now = millis();
    uint32_t configRefreshInterval = tracking.active
      ? TRACKING_CONFIG_REFRESH_INTERVAL_MS
      : CONFIG_REFRESH_INTERVAL_MS;

    if (now - lastConfigAt >= configRefreshInterval) {
      lastConfigAt = now;
      fetchConfig();
      now = millis();
    }

    if (
      tracking.active
      && now - lastTrackingUploadAt >= tracking.uploadIntervalMs
    ) {
      lastTrackingUploadAt = now;
      uploadTrackingBatch();
      now = millis();
    }

    bool stagedBatch = pendingObservationCount > 0;
    bool mayStageBatch = (tracking.active || normalUploadReady) && !gattWorkerBlocksUpload();
    if (
      now - lastUploadAt >= scannerConfig.uploadIntervalMs
      && (stagedBatch || mayStageBatch)
    ) {
      lastUploadAt = now;
      uploadBatch();
      now = millis();
    }

    if (now - lastHeartbeatAt >= HEARTBEAT_INTERVAL_MS) {
      lastHeartbeatAt = now;
      sendHeartbeat();
    }

    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

void setup() {
  Serial.begin(115200);
  uint64_t hardwareId = ESP.getEfuseMac();
  bootId = String("boot-") + String(static_cast<uint32_t>(hardwareId >> 32), HEX)
    + String(static_cast<uint32_t>(hardwareId), HEX) + "-" + String(esp_random(), HEX);
  observationBufferMutex = xSemaphoreCreateMutex();
  trackingConfigMutex = xSemaphoreCreateMutex();
  serialControlMutex = xSemaphoreCreateMutex();
  gattWorkerMutex = xSemaphoreCreateMutex();
  if (
    observationBufferMutex == nullptr
    || trackingConfigMutex == nullptr
    || serialControlMutex == nullptr
  ) {
    Serial.println("[firmware] Fatal: transport synchronization allocation failed.");
    while (true) {
      delay(1000);
    }
  }
  if (gattWorkerMutex == nullptr) {
    Serial.println("[firmware] GATT enrichment disabled: mutex allocation failed.");
  }
  setupBle();
  BaseType_t transportCreated = xTaskCreate(
    transportTask,
    "transport",
    TRANSPORT_TASK_STACK_SIZE,
    nullptr,
    1,
    &transportTaskHandle
  );
  if (transportCreated != pdPASS) {
    Serial.println("[firmware] Fatal: transport task allocation failed.");
    while (true) {
      delay(1000);
    }
  }
}

void loop() {
  applyPendingSerialControl();
  processGattWorker();
  applyTrackingModeChange();
  uint32_t now = millis();

  TrackingConfig tracking = snapshotTrackingConfig();
  if (tracking.active) {
    NimBLEScan *scan = NimBLEDevice::getScan();
    if (!scan->isScanning()) {
      startTrackingScan();
      now = millis();
    }
    if (now - lastTrackingCycleAt >= scannerConfig.scanIntervalMs) {
      scanCycle++;
      lastTrackingCycleAt = now;
    }
  } else {
    if (now - lastScanAt >= scannerConfig.scanIntervalMs) {
      lastScanAt = now;
      runScan();
      processGattWorker();
      now = millis();
    }
  }

  delay(20);
}
