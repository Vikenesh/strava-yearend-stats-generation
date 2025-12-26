import os
from groq import Groq

def test_grok_api():
    # Initialize the Groq client
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    
    # Test chat completion
    chat_completion = client.chat.completions.create(
        messages=[
            {
                "role": "system",
                "content": "You are a helpful assistant.",
            },
            {
                "role": "user",
                "content": "Just say hi and hello world and nothing else.",
            }
        ],
        model="mixtral-8x7b-32768",
    )
    
    # Print the response
    print("Response from Grok API:")
    print(chat_completion.choices[0].message.content)

if __name__ == "__main__":
    if not os.getenv("GROQ_API_KEY"):
        print("Error: GROQ_API_KEY environment variable not set")
        print("Please set it with: export GROQ_API_KEY='your-api-key'")
    else:
        test_grok_api()
