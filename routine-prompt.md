## Routine prompt (business account)

Once the skill below is in a repo this environment has attached, create the Routine
with this short prompt instead of the old inline one:

```
Run the `new-light-sermon-recap` skill (invoke it via the Skill tool) to do this
week's New Light Church sermon-clip + Instagram recap automation. If it doesn't
appear in your available skills list, read .claude/skills/new-light-sermon-recap/SKILL.md
directly in the attached repo and follow it exactly. This is a fully automated weekly
run with nobody watching — do not pause to ask questions.
```

Routine settings (unchanged from before):
- name: "New Light — Monday sermon clip + Instagram recap"
- cron_expression: "0 12 * * 1"  (or "CRON_TZ=America/New_York 0 8 * * 1" to pin to 8am Eastern year-round)
- create_new_session_on_fire: true
- initiation: human_request
- connectors: ["Gmail", "OpusClip"]
