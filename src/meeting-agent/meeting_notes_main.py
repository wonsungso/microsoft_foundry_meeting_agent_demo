from dotenv import load_dotenv
from meeting_agent.runtime import build_chat_model, build_repository, run_server
from meeting_agent.specialists import build_meeting_notes_graph

load_dotenv()

if __name__ == "__main__":
    run_server(build_meeting_notes_graph(build_chat_model(), build_repository()))