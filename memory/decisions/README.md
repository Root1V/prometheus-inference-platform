# Architecture decisions

Seven records of decisions that shaped the platform, and the reasoning behind
each. They exist so a choice does not have to be re-derived — or, worse,
re-litigated from memory by whoever is in the room.

## They are immutable, and that is the point

**An ADR is never edited to match the present and never deleted when it stops
being true.** A decision that was made *was* made; rewriting it destroys the
only record of why the code looks the way it does, and deleting it makes the
code look arbitrary.

What changes instead is a **status note at the top**, added on review. That is
the standard ADR lifecycle, and the vocabulary here is:

| Note | Meaning |
|---|---|
| **still current** | Verified against the code on that date; follow it |
| **premise superseded, mechanism still current** | The thing it decided about changed; the technique it chose is still right |
| **deliberately amended** | A later decision narrowed it on purpose, and names which |
| **scope narrower than written** | It over-claimed, usually "all environments" or "all endpoints" |
| **half current** | Part holds, part does not — the note says which, and whether the gap is a decision or a defect |
| **obsolete specifics** | The principle holds; named details are out of date |

## Every record states when it was last checked

All seven were reviewed on **2026-09-26** against the running code, and five
needed a note. The failures were not subtle:

- `podman-over-docker` claimed all environments used containers; zero containers
  were running and the development stack is bare-metal.
- `manager-owns-registry` named `registry.yaml` as the artefact; the manager has
  used SQLite for months. Its rejected-alternatives table rejects a SQLite
  registry, which the manager then became.
- `redis-for-state` claimed all shared state is in Redis; two things are not, and
  one of them is a bug.
- `llama-cpp-bare-metal` was written when there was one engine. There are seven.
- `openai-api-compatibility` said all inference endpoints keep OpenAI's shape;
  `PRM-136` added one that deliberately does not.

**None of that was visible from reading the documents**, which is the reason for
the convention. An ADR with no review date is an ADR nobody has checked, and it
reads exactly like one that is true. `scripts/check_spec_references.py` fails the
push when a record here carries no `Reviewed <date>` line.

## Writing a new one

Same shape as the existing records — context, decision, rationale, consequences,
rejected alternatives — plus a `Reviewed <today>` line, because a record written
today *has* been checked against the code today. Then link it from the roadmap
entry that motivated it rather than inlining the reasoning there (`CLAUDE.md`).

The specs in `../specs/` are a different thing: a historical record of what was
built, cited 288 times by the source. These are why it was built that way.
