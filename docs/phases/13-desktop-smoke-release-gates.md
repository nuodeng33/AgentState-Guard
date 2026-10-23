# Phase 13 — Strict desktop smoke and release gate accounting

- Base:e8cab97; branchfix/release-desktop-smoke. User requested advancing the release plan and an explicit remaining-work count.
- Requirement IDs: ASG-REL-001/002, ASG-TEST-001/004. Entry: scripts/test-desktop.ps1 swallowed health failures, lacked ownership/cleanup checks and could printDone despite failure.
- Scope: whole-script fail-closed fix and actualPowerShell regression. Fixed applicationport8787; deterministic defaultreleasepath or explicitBinaryPath; bounded readiness; ownedloopback200+JSONok; mainmustsurvivehealth; error+cleanupfailure exit1.
- Process safety: ownedPID+StartTime cannotbeoverwritten; initial childbirth>=parentbirth; CIM creationidentity matchesProcess atmicrosecondprecision; snapshotparentidentity plusliveparent rechecked beforeadoption. Cleanupchild-first individual sameidentity Stop, no taskkill/T orglobalnamekills. Unknown/reusedprocessesarenotstopped; leaksfail.
- Tests: officialportablePowerShell7.4.6 archiveSHA256 checked. IsolatedXDGcache/config/data/state pertest; noHOMErewrites/globalinstall. WholeproductionPS script runs withsimulatedOS/process/net/sleep boundaries; 20cases covernormalhealth,HTTP/JSON/statuserrors,readiness,early/lateexit,portownership,PID/parentreuse,cleanup/leaks andcombinedprimary+cleanuperrors. ValidoriginalRED11fail/3pass; newownershipRED3fail/16pass; parentreuseRED1fail; currenttarget20pass.
- CI: Windows lightweightjob collectsnewscript tests + existingcleanup + processdeath; installedMSI executesactualstrictscript beforeuninstall. Remoteexecution remainsrequired. LinuxPS regression is notnativeWindows/WebView proof.
- Exit: target+fullbackend pass, projectJUnit audit, independentreview, localatomiccommit. NoUI/schema/model/providerchanges andnopush.
- Rollback: revertthisisolatedcommit, basee8cab97. Noreset/clean/migration.
- External gates: nativeCodexactualcalls awaitexplicitcredentialreuse choice; Windowsnative/releaseartifacttesting needsWindows; exactheadremoteCI requiresliftingpriorno-pushconstraint; physicaldesktop/mobile acceptance needsrealdevice. Noexpired/historicalCI presentedascurrent.

## Evidence

- ValidPS originalscript11fail/3pass; final20pass10.49s; full1369passed/26skipped/0fail,85.38s, projectJUnit1395cases. OldWindows-only9cleanup skipped here, explicitlynotcountedpassed.
- Independentreview closedchild/parentPID reuse andlatehealth-exit defects. Sourcehashes unchangedduringfullverification. Existing684deprecationwarnings remain.
- Userexplicitlyapproved existingCodexlogin reuse. Realattempt b32b9ebc-40d3-4ff7-a9cc-90e2e557c222 FAIL atsessioncreation/EXECUTION_STORE_IDENTITY_CHANGED, no modelrunstarted. Newrootcause remainsnextafterthisatomicfix.
