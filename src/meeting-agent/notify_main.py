import os

from dotenv import load_dotenv
from meeting_agent.runtime import build_mailer, run_server
from meeting_agent.specialists import build_notify_graph

load_dotenv()

if __name__ == "__main__":
    run_server(
        build_notify_graph(
            build_mailer(),
            os.environ.get("MAIL_RECIPIENT", "demo@example.com"),
        )
    )