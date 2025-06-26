import argparse

from dynamo_debugger.main import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dynamo Python Node Debugger")
    parser.add_argument(
        "dyn_file",
        type=str,
        help="Path to the .dyn file to debug",
    )
    args = parser.parse_args()
    main(args.dyn_file)
