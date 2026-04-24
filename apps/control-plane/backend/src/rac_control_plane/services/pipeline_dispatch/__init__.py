# pattern: Imperative Shell
"""Pipeline dispatch service.

Provides the three collaborators for triggering the rac-pipeline:
- payload.build_dispatch_payload   (Functional Core)
- secret_mint.mint_callback_secret (Imperative Shell)
- github.dispatch                  (Imperative Shell)
"""
