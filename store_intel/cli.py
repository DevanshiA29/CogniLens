from __future__ import annotations

import argparse
import json

from store_intel.pipeline import StoreIntelligencePipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Agentic Store Intelligence pipeline")
    parser.add_argument("--video", help="Path to one CCTV video")
    parser.add_argument("--folder", help="Path to a folder of CCTV videos")
    parser.add_argument("--demo", action="store_true", help="Generate and process demo CCTV footage")
    parser.add_argument("--store-id", default="STORE_BLR_002")
    parser.add_argument("--camera-id", default="CAM_ENTRY_01")
    parser.add_argument("--layout", help="Path to store_layout.json")
    parser.add_argument("--pos", help="Path to pos_transactions.csv")
    parser.add_argument("--db", default="data/store_intel.db")
    args = parser.parse_args()

    pipeline = StoreIntelligencePipeline(args.db)
    if args.demo:
        result = pipeline.run_demo(args.store_id, args.camera_id)
    elif args.folder:
        result = pipeline.process_folder(args.folder, args.store_id, args.layout, args.pos)
    elif args.video:
        result = pipeline.process_video(args.video, args.store_id, args.camera_id, args.layout, args.pos)
    else:
        parser.error("Provide --demo, --video, or --folder")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
