PRAGMA application_id = 1195984705;
PRAGMA user_version = 1;

CREATE TABLE projects (
    project_id TEXT PRIMARY KEY NOT NULL,
    target_locator TEXT NOT NULL UNIQUE,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('git', 'non-git'))
) WITHOUT ROWID
;

INSERT INTO projects(project_id, target_locator, target_kind) VALUES
    ('project_12345678-1234-4234-9234-123456789abc', '/fixture/targets/git-one', 'git'),
    ('project_87654321-4321-4432-a321-cba987654321', '/fixture/targets/non-git-two', 'non-git');
