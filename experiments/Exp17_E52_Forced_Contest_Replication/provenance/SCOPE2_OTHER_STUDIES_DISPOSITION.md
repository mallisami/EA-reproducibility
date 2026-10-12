# Scope-2 studies not promoted to manuscript claims

The Scope-2 campaign also executed E51, E53, and E55. Their outputs were retained and were
reviewed rather than discarded, but they are not used to broaden the manuscript's central
claims:

- **E51 (HA K3s):** execution stopped after 3,234/5,344 records following 20 consecutive
  connection-refused infrastructure errors. The retained result archive does not provide the
  RequestResponse audit evidence needed for the paper's protected-boundary challenge
  attribution. It is therefore not used as evidence of independent HA confirmation.
- **E53 (grace matrix):** the two nominal 5-s workload modes had nearly identical removal
  timing, and both 30-s runs were stopped early by the harness's 30-s old-Pod disappearance
  limit. It is therefore not used to replace the completed E43 graceful-termination study.
- **E55 (read-back diagnostic):** 1,276/1,287 asynchronous A4d writer read-backs returned
  HTTP 404 and are correctly treated as inconclusive. In the 11 observable cases the written
  value persisted and the corresponding conditional DELETE conflicted. E55 is retained as a
  measurement diagnostic, not as proof of non-commit.

These bounded dispositions prevent incomplete or diagnostic studies from being overstated.
