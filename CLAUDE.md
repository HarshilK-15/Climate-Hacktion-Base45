# Shipless - rules for every Claude Code session

- Only edit files in YOUR folder. Owners: firmware/ = Elec A, engine/ = Elec B,
  data/ + guard/ = Data Sci, agent/ = Mech A, server/ + web/ + tools/ = Mech B.
- Never change the data formats in contracts/FORMATS.md without asking Mech B and the owner.
- Every assumed number in the code gets a comment saying so and where it should come from.
- Engine or guard changes need passing tests: python -m engine.test_sim and python -m guard.test_rules
- Never put API keys or passwords in code. Use environment variables.
- Run the app: python -m server.app  (then open http://localhost:8000)
- Fake sensor: python tools/fake_device.py http://localhost:8000/api/readings
