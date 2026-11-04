# BBS-Pipeline v3

Clean-room v3 North American Breeding Bird Survey (BBS) Spatial & Ecological ETL Pipeline.
Decouples a Two-Way Streamlit Visual Workbench (`ui/app.py`) from a headless Polars/Shapely spatial ETL backend using frozen Pydantic v2 JSON contracts (`config/guilds.json`, `config/protocol.json`, `config/spatial.json`).
