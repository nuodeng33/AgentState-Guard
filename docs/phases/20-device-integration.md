# Phase20 — Device integration closure

Requirement ASG-CLOSE-004, localbase98c69e0. Existing backend pairing contract requires exact server SAS and a durable binding before consumed; desktop/Android currently unwired/broken. No new publicAPI/schema, permission weakening, manualIP onboarding, remote restore/apply or provider settings.

Design reviewed independently: retain invitationfacts and reject late polls; SAS equality/explicitdualconfirm; affirmative same-side confirmation idempotent onlywhile eligible andtokenvalid; gateway/sharedmanager RLock acrosslisteners; binding registry capacity/duplicate/insert/affectedrows in oneBEGINIMMEDIATE transaction; TTL checked afterlock andbeforecommit; consume onlyafter durablecommit with internally validated finalize. No complete replay or keyreplace. Android scanner/navigation/actions reuseexistingcontracts and explicit decisions; exact parser and serveraction_ref remain authoritative.

Affected: DeviceLinkgateway/pairing/persistence/tests; desktopadapter/DevicesPage/DTO/tests; Android UI/repository/scanner and minimal projectdependency/tests. Scanner officialprimarydocs inspected. No globalSDK/plugin install.

Risks: confirmation order, lostresponse, TTL crossingDBlock, duplicate/concurrentcomplete, revoke/cancel/disable race, QRpollstaleness, Android missingplayservices. Tests preserve existing expiry/no-sideeffect/securityregressions, add adversarialbinding tests and durable reopen; actualbrowserCore+signed mobileprotocol simulation; Android compile/tests viaavailable projecttoolchain/CI. Physical camera/KeyStore/LAN acceptance remainsdistinct. Rollback98c69e0, no DBmigration.

Tasks: design[x]; backend tests+fix[x]; desktop/Android UI[x; Androidcompile pendingCI]; realbrowserprotocol[ ]; AndroidCI[ ]; finaltests/docs/push[ ].
