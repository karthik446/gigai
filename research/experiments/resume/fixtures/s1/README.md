# S1 code-review fixture

Review goal: reconcile enrollment events into application status updates and
publish the result. The implementation must tolerate retries and large mixed-event
backlogs while preserving the existing multi-tenant and privacy guarantees.

The happy-path tests are intentionally not a complete specification. Review the
implementation against the goal and the contracts expressed by its protocols.
