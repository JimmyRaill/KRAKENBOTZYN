from commands import handle

print("KrakenTradingBot shell — type 'help' for commands, Ctrl+C to quit")

while True:
    try:
        cmd = input("> ")
        out = handle(cmd)
        print(out)
    except KeyboardInterrupt:
        print("\nbye")
        break
