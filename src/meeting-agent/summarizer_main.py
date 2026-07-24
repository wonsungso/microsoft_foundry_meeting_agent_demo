from dotenv import load_dotenv
from meeting_agent.runtime import build_chat_model, run_server
from meeting_agent.specialists import build_summarizer_graph

load_dotenv()

if __name__ == "__main__":
    run_server(build_summarizer_graph(build_chat_model()))