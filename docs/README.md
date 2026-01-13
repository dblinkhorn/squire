Project Overview

Project Name

Squire

Purpose

This is a self-hosted, modular, AI-assisted personal organization and reminder system that captures notes frictionlessly (via Discord DM), interprets them into structured entities, stores them durably in a git-backed archive, indexes them locally for fast queries, and proactively or interactively surfaces relevant information.

The system prioritizes durability, trust, low cognitive overhead, composability, and open-source deployability. Git is the durable archive and source of truth, while SQLite is a rebuildable derived index. AI output is never authoritative without auditability. Surfacing is the primary product rather than storage, and all automation is explainable and repairable.

Canonical objects are the current truthy records used for querying, surfacing, and maintenance. They are the only mutable artifacts; everything else is immutable and versioned.
