# Local Policy / Training Ideas

This is speculative and should stay careful.

## Short-term: policy prompting

Use Gemma as a planner/personality model that sees:
- user utterance
- simplified robot state
- recent action history
- allowed actions

It returns JSON action plans. We can improve behavior with examples from teleop logs without training weights.

## Medium-term: imitation from demonstrations

Use controller sessions as demonstrations:
- Human controls Vector through tasks.
- Logs capture state/action pairs.
- Build a small policy model or retrieval library: "when state + instruction looks like X, do demonstrated sequence Y".

This could be done before any model fine-tuning:
- cluster episodes by task
- retrieve nearest examples
- include examples in Gemma prompt
- ask Gemma for bounded action plan

## Long-term: learned low-level policy

Potentially train a small model on controller traces, but only after enough clean data exists. For Vector, deterministic control + retrieval may outperform trying to learn motor policy from little data.

## Safety principle

Never train/enable a policy that can bypass:
- speed caps
- duration caps
- emergency stop
- deadman behavior for teleop
- physical supervision requirements

## Evaluation tasks

Start with tiny tasks:
- greet Rob from charger
- look left/right and say status
- drive forward 10cm and stop
- turn toward sound/face if SDK exposes enough state
- celebration routine
- return/dock behavior if reliable
