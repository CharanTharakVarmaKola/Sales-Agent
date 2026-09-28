"""Phase-B inference package. Implements Gate 3 (pause-first inference).

Convention (carried MINOR 1 from B1): no I/O inside pure decision paths.
draft() implementations must check paused state before any network I/O;
transaction bodies and transition conditions take note likewise.
"""
