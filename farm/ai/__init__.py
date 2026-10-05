"""AI orchestration through the Farm: the main AI runs other AIs as workers, checks on them, reads each
exact result and sends follow-ups to the same worker.

Layers (each one small; the CLI transport underneath is the M3e executors, untouched):

* ``accounts``      which AI accounts exist, which can take work now, how busy they are, which to pick
* ``jobs``          the non-blocking layer: ``JobStore`` (the ``ai_jobs`` table) and ``JobManager`` (queue,
                    per-account and per-pool limits, runner, retry on another account, cancel,
                    restart recovery)
* ``conversations`` the ``ai_conversations`` table: one thread with one worker, pinned to its account
* ``results``       exact results: file store, JSON-schema check, ``git status`` of an edit
* ``failures``      the failure kinds a caller sees and how a router error maps to them

A job runs through ``farm.resources.router.route`` pinned to its account, so quota reservation, commit, cost,
health, alerts and the ``runs`` trajectory are the router's, exactly as for the blocking ``ask_ai``. A second
transport (ACP: persistent streaming sessions) plugs in behind ``JobManager`` / the conversation API without
changing the tools.
"""
