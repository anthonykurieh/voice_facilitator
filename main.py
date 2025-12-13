import messagebird

# 1) Your LIVE API key (from Developers → Access keys)
API_KEY = "8eclWSIhy85mM9bHjKY7Zlh1LY6l3cSwLm6X"

# 2) Destination: your UAE mobile in E.164 format (no spaces)
TO_NUMBER = "+971504973929"  # example

def make_voice_test_call():
    # Create client with your live key
    client = messagebird.Client(API_KEY)

    try:
        # This uses the Voice Messaging API (simple TTS phone call)
        msg = client.voice_message_create(
            TO_NUMBER,
            "Hello. This is a MessageBird test call to check if voice works in the United Arab Emirates.",
            {
                "voice": "female",      # or "male"
                "language": "en-gb",    # pick any supported TTS language
                "repeat": 1
            }
        )

        print("Created voice message:")
        print("  id:", msg.id)
        print("  recipients count:", msg.recipients.total_count)
        print("  status of first recipient:", msg.recipients.items[0].status)

    except messagebird.client.ErrorException as e:
        print("Error from MessageBird API:")
        for err in e.errors:
            print(f"- [{err.code}] {err.description}")


if __name__ == "__main__":
    make_voice_test_call()