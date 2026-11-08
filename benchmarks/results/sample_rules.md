# DocHeal benchmark — engine `rules`

21 labelled scenarios against `examples/sample_project`.

| Metric | Value |
|---|---|
| true positives | 16 |
| false positives | 0 |
| false negatives | 0 |
| precision | 1.0 |
| recall | 1.0 |
| f1 | 1.0 |
| mode accuracy | 1.0 |
| correction quality | 1.0 |
| suspects checked | 39 |

| Scenario | Category | TP | FP | FN | Mode ok | Fix checks |
|---|---|---|---|---|---|---|
| rename-parameter | signature | 1 | 0 | 0 | 1/1 | 3/3 |
| change-default-value | signature | 1 | 0 | 0 | 1/1 | 1/1 |
| rename-method | signature | 1 | 0 | 0 | 1/1 | 1/1 |
| config-field-default | config | 1 | 0 | 0 | 1/1 | 1/1 |
| module-constant | config | 1 | 0 | 0 | 1/1 | 1/1 |
| env-var-default | config | 1 | 0 | 0 | 1/1 | 1/1 |
| endpoint-route | api | 1 | 0 | 0 | 1/1 | 2/2 |
| cli-option-rename | cli | 1 | 0 | 0 | 1/1 | 3/3 |
| cli-command-rename | cli | 1 | 0 | 0 | 1/1 | 2/2 |
| default-poll-interval | signature | 1 | 0 | 0 | 1/1 | 1/1 |
| rename-class | signature | 2 | 0 | 0 | 2/2 | 2/2 |
| remove-endpoint | api | 1 | 0 | 0 | 1/1 | 0/0 |
| new-required-parameter | signature | 2 | 0 | 0 | 2/2 | 0/0 |
| remove-cli-command | cli | 1 | 0 | 0 | 1/1 | 0/0 |
| comment-only | negative | 0 | 0 | 0 | 0/0 | 0/0 |
| whitespace-only | negative | 0 | 0 | 0 | 0/0 | 0/0 |
| docstring-only | negative | 0 | 0 | 0 | 0/0 | 0/0 |
| test-file-only | negative | 0 | 0 | 0 | 0/0 | 0/0 |
| internal-refactor | negative | 0 | 0 | 0 | 0/0 | 0/0 |
| new-optional-parameter | negative | 0 | 0 | 0 | 0/0 | 0/0 |
| new-function | negative | 0 | 0 | 0 | 0/0 | 0/0 |
