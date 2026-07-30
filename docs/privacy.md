# Privacy And Authorized Use

## Data Sensitivity

Bluetooth advertisements are radio broadcasts, but collecting them over time can reveal device presence, recurrence, and movement between monitored locations. Raw payloads, addresses, names, GATT serial values, scanner coordinates, focused RSSI samples, Walk GPS paths, timestamps, aliases, notes, and correlation decisions must be treated as operationally sensitive data.

Operate the system only where Bluetooth monitoring is authorized. Deployment owners are responsible for notice, consent, lawful basis, retention, access, and incident handling under the rules that apply to the installation.

## Data-Minimization Rules

- Collect fields required for scanner operation, diagnosis, and approved analysis.
- Do not fabricate or attach a person's identity to a Bluetooth record.
- Do not place personal, credential, medical, or employment information in aliases, tags, notes, installation names, or maintenance notes.
- Keep raw characteristic reads bounded to the approved GATT enrichment implementation.
- Do not use API or test-console output to infer ownership from a company ID, product name, location, or repeated presence.
- Keep unresolved random addresses separate unless an approved evidence path justifies correlation.
- Treat Apple Continuity transition evidence as sensitive. The backend hashes persistent protocol fields in correlation records, keeps proposals non-automatic, and retains raw bytes only in the authorized observation history.
- Hiding transient broadcasts from Devices or Locations is a visibility policy, not deletion or anonymization.
- Start a focused session only for an authorized operational purpose and stop it when the measurement is complete.
- Use Walk mode only when recording the scanner route is authorized; browser geolocation can expose a more detailed operator/device path than normal fixed scanner data.
- Disable or remove collection features that are not justified for the deployment.

## Access Boundary

Operator/read APIs have no application login in the current backend. This is not a security boundary. Bind locally by default and use network segmentation, firewall rules, VPN, or a reverse proxy policy when other hosts require access. The bundled test console must not be deployed as a production frontend.

Scanner APIs require bearer tokens. Registration should require `SCANNER_REGISTRATION_SECRET`. Tokens and secrets belong in protected host configuration, not firmware source, URLs, browser storage, documentation, diagnostics, or support attachments.

## Storage And Retention

SQLite files, PostgreSQL backups, exported diagnostics, logs, and `.env` backups must inherit the same protection as the live system. Database backups include historical device observations even though scanner tokens are stored only as hashes.

Automatic cleanup is implemented only for focused tracking samples, Walk positions, and old terminal tracking sessions. Normal observations and location estimates are not automatically deleted. Operators must monitor growth and establish an authorized cleanup process for normal history. Deletion work must preserve required audit events and must be verified against backup and legal-retention requirements.

## Sharing And Incident Handling

Before sharing a failure report, remove bearer tokens, registration secrets, environment files, unrelated raw Bluetooth payloads, precise scanner coordinates, operator notes, and device identifiers that are not required to reproduce the issue.

A suspected token disclosure requires bridge credential rotation and review of scanner/API activity. A suspected database disclosure requires preserving incident evidence, restricting service access, identifying affected retention windows and locations, and following the deployment owner's notification process.

## Accuracy And Human Decisions

RSSI, movement, proximity, vendor, category, and address correlation contain uncertainty. They must not be used as the sole basis for disciplinary, safety-critical, legal, or identity decisions. The raw evidence, method, confidence, physical limitations, and operator decision history must remain available for review.
