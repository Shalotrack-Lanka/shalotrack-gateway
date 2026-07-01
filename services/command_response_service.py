from models.command_response import CommandResponse
from repositories.command_response_repository import (
    CommandResponseRepository
)
from utils.logger import log


def handle_command_response(
    device_id,
    imei,
    response
):

    log(
        f"📨 {response['command']} Response Received"
    )

    command_response = CommandResponse(
        device_id=device_id,
        command=response["command"],
        raw_response=response["raw_text"],
        parsed_data=response["data"]
    )

    CommandResponseRepository.save(
        command_response
    )