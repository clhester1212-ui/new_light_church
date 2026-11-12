"""Start the New Light PTZ Tracker: python run.py  (then open http://localhost:8890)"""
import argparse

from nlptz.server import serve

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to allow other computers on the church network")
    ap.add_argument("--port", type=int, default=8890)
    a = ap.parse_args()
    serve(a.host, a.port)
