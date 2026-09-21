---
name: dialpad-administration
description: Inspect and perform authorized Dialpad administration for users, offices, numbers, contact centers, routing, IVR, devices, access policies, meetings, and live call controls through documented APIs.
---

# Dialpad Administration

Use `../dialpad-api/SKILL.md` and query only the relevant catalog family. The catalog exposes operations; it does not establish that the current account has the necessary role or license.

## Discover and inspect

Resolve the company and exact object IDs before changes. Families include users/offices, numbers and assignments, rooms/devices, callcenters/operators, departments/operators, coaching teams, callrouters/custom IVRs, access-control policies, dispositions/labels, meetings, and call actions. Read linked records when an assignment or route affects another entity. Select GET inspection first; listing billing plans is not permission to purchase anything.

For a requested change, capture the relevant current values and prepare a concrete diff. Distinguish additive changes from replace/set semantics. In particular, label replacement must preserve labels the user did not ask to remove. Read the selected operation's full input schema and constraints. Do not fill an unknown routing, emergency address, retention, access, or recording-policy value by guesswork.

## Execute authorized changes

Preview the exact request body, target, and method. Follow the user's existing authorization for the exact change; ask only when a meaningful decision remains or a tool-specific approval is required. An installation request or a reporting request is not authorization for tenant configuration changes.

After success, GET the affected record and relevant dependent state to verify the intended value. Retain a minimal rollback description where the operation is reversible. For deletion/unassignment, distinguish removing a relationship from deleting the underlying user/number. Changes affecting access, emergency routing, recording, number assignment, or active calls require precise targets and clear authorization.

## Live call actions

Call initiation rings devices or starts the documented IVR flow; it does not create an autonomous speaking agent. Transfers, hangups, participant additions, queue assignment/unassignment and recording toggles affect real conversations. Verify the intended call and operator immediately before execution. The concluded-call detail endpoint alone does not prove a call is currently active; use available current-status evidence and do not substitute an old call with the same phone number.

Features absent from the public catalog, such as a particular UI-only policy or porting procedure, need the account's established administration workflow or official UI. If the separate `dialpad-contact-center-administration` skill is installed, preserve it and use it for its specialized procedures; this pack does not overwrite it.
