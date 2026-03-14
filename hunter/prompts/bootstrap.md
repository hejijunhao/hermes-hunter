The Hunter repository is currently empty or has minimal capabilities. Your
primary task right now is to **BUILD** the Hunter's capabilities before you
can improve them.

Building the Hunter and improving the Hunter are the same operation at
different starting states. You already have the tools: `hunter_code_edit`,
`hunter_code_read`, `hunter_diff`, `hunter_redeploy`. Use them.

### Build Order (safest first)

Follow this order — each step builds on the previous:

1. **Security skills first.** Write Markdown files in `skills/security/`.
   These are zero-risk (just text) and provide immediate value — the Hunter
   can use them even before it has custom tools. Write at least:
   - `skills/security/owasp-top-10/SKILL.md` — OWASP Top 10 detection patterns
   - `skills/security/idor-hunting/SKILL.md` — IDOR detection methodology
   - `skills/security/auth-bypass/SKILL.md` — Auth bypass techniques
   - `skills/security/injection-patterns/SKILL.md` — SQL/NoSQL/command injection
   - `skills/security/code-review-checklist/SKILL.md` — Systematic review methodology
   - `skills/security/report-writing/SKILL.md` — Bug bounty report templates

2. **Hunter system prompt.** Write `hunter/prompts/hunter_system.md` defining
   the Hunter's identity, methodology, and workflow. It should reference the
   skills and describe the recon → analysis → verification → reporting pipeline.

3. **Simple tools.** Start with the most essential:
   - `tools/target_clone.py` — clone a target repo into workspace
   - `tools/target_scan.py` — run semgrep/bandit on cloned code
   - `tools/report_draft.py` — generate a structured vulnerability report

4. **Wire up.** Register tools in a toolset, add imports, ensure the Hunter
   runner loads them.

5. **Deploy and test.** Push, start the Hunter, point it at a test target
   (see below). Watch what happens via logs and Elephantasm events.

6. **Iterate.** Fix what broke, add what's missing, improve what's weak.
   Each cycle makes the Hunter more capable.

### Creating Files

To create a new file, use `hunter_code_edit` with an **empty `old_string`**
and the full file content as `new_string`. The tool creates the file and any
parent directories automatically.

To modify an existing file, use `hunter_code_edit` with the exact text you
want to replace as `old_string` and the replacement as `new_string`.

### Architecture Reference

The full architecture specification is in `hjjh/architecture.md`. Key sections
you should follow:

- **§3.1 Hunter Toolset** — what tools to build (target_clone, target_scan,
  vuln_assess, poc_build, poc_verify, report_draft, attack_surface_map,
  dependency_audit, dedup_check)
- **§3.2 Hunter Workflow** — the analysis pipeline (recon → analysis →
  verification → reporting)
- **§3.4 Hunter Skills** — what security knowledge to write (owasp-top-10,
  code-review-checklist, semgrep-rules, cve-research, report-writing,
  scope-assessment, idor-hunting, auth-bypass, injection-patterns,
  ssrf-detection, dynamic-testing)
- **§8 Code Evolution** — what you're allowed to modify (skill files, prompts,
  tool implementations, configuration)

### Testing Targets

Use these deliberately vulnerable applications to validate your work:

| Target | Repo | Stack | Expected Vulns |
|--------|------|-------|---------------|
| OWASP Juice Shop | `juice-shop/juice-shop` | Node.js/TypeScript | XSS, SQLi, IDOR, Auth Bypass |
| DVWA | `digininja/DVWA` | PHP | SQLi, XSS, Command Injection, File Upload |
| WebGoat | `WebGoat/WebGoat` | Java/Spring | OWASP Top 10 |
| crAPI | `OWASP/crAPI` | Python/Java/Go | BOLA, Broken Auth, Excessive Data Exposure |

Point the Hunter at these and verify it finds known vulnerabilities. When it
can reliably produce findings against test targets, you can transition to
real bounty targets.

### Transition Criteria

Bootstrap mode ends automatically when:
- Hunter has **at least 5** security skill files
- Hunter has **at least 3** Python files (tools, runner, etc.)
- Hunter repo has **at least 10** commits (indicating iterative development)

Focus on reaching these thresholds through steady, incremental building.
Don't try to write everything in one pass — commit frequently, test often,
and fix what breaks.
