#!/usr/bin/env python

import os
from pathlib import Path
from time import sleep

from flask import Flask
from flask import request
from flask.logging import default_handler

from logger import logger

from app.play import gstreamer_webrtc_client
from app.play import video_recorder
from app.play import application
from app.utils.utils import traced

video_endpoint = Flask("RMCLabs")
video_endpoint.logger = logger

video_endpoint.logger.removeHandler(default_handler)

@traced(logger.info)
@video_endpoint.route("/ready", methods=["GET"])
def ready():
    return {
        "STATUS": f"OK",
    }

@traced(logger.info)
@video_endpoint.route("/list_cameras", methods=["GET"])
def list_cameras():
    cameras = application.cameras
    return {
        "STATUS": f"OK",
        "cameras": list(
            range(len(cameras))
        ),  # TODO: actually map these to nvstreammux sourcepads
    }

@traced(logger.info)
@video_endpoint.route(
    "/focus_camera/<camera_id>", methods=["GET"]
)  # TODO use post instead
def focus_camera(camera_id: int):
    response = application.focus_camera(int(camera_id))
    return {
        "STATUS": f"???",
        "response": response,
    }

@traced(logger.debug)
@video_endpoint.route("/record/<int:source_id>", methods=["GET"])
def start_recording(source_id):
    recorded_video_path = video_recorder.record(source_id)
    return {
        "STATUS": f"RECORDING VIDEO",
        "video_path": recorded_video_path,
    }

@traced(logger.info)
@video_endpoint.route("/start/<peer_id>", methods=["GET"])
def start_streaming(peer_id):

    errs = []
    for _ in range(10):
        try:
            gstreamer_webrtc_client.open_streaming_connection(peer_id)
            return {"STATUS": "RUNNING"}
        except Exception as e:
            errs.append({"ERROR_TYPE": str(type(e)), "ERROR": str(e)})
            sleep(1)
    return {"ERROR_TYPE": "MULTIPLE ERRORS", "ERROR": errs}





if __name__ == "__main__":
    certs_path = Path(os.environ["CERTS_PATH"])
    video_endpoint.run(
        debug=False,
        host=os.environ["FLASK_RUN_HOST"],
        port=os.environ["FLASK_RUN_PORT"],
        threaded=False,
        ssl_context=(str(certs_path / "cert.pem"), str(certs_path / "key.pem")),
    )
