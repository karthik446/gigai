# G19 mutation report

The focused mutation harness \`tools/run_g19_mutation.py\` copied
\`src/gigai/target_effect.py\` into disposable source trees and ran one focused
integration test per mutant. No mutation was applied to the working tree.

| Mutant | Removed guard | Result |
|---|---|---|
| \`target-head-revalidation\` | Changed Git \`HEAD\` is accepted | Caught by changed-HEAD refusal |
| \`target-path-containment\` | Target path bypasses symlink/traversal checks | Caught by path/symlink refusal |
| \`source-digest-revalidation\` | Replacement source may change after authorization | Caught by source-digest refusal |
| \`dirty-target-refusal\` | Dirty target may enter preparation | Caught by clean-target refusal |
| \`after-digest-verification\` | Post-exposure bytes are not verified | Caught by ambiguous recovery blocking |
| \`atomic-exposure\` | Replacement uses a non-atomic write | Caught by the static atomic-exposure guard |
| \`exposed-recovery-decision\` | Exact after-state is not recovered as applied | Caught by exposed-record recovery |

Result: \`mutation_killed=7/7\`.
