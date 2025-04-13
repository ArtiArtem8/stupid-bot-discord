# StupidBot

StupidBot is a modern Discord bot built with [discord.py](https://github.com/Rapptz/discord.py) v2.x. This project is a rewritten version of an older bot (based on discord.py v1.7) that now utilizes modern patterns including slash commands and cogs. It integrates several features such as birthday management, WolframAlpha integration, message processing, and image utilities.

## Features

- **Modern Discord.py v2:** Uses the latest features like slash commands and application command trees.
- **Birthday System:**  
  - Set, update, and remove birthday records.
  - Automatically sends birthday wishes and assigns a birthday role.
  - Removes birthday role on non-birthday days.
- **WolframAlpha Integration:**  
  - Solve mathematical problems and plot functions.
- **Message Processing:**  
  - Fuzzy matching for morning and evening greetings.
  - Randomized answer generation for questions.
- **Image Utilities:**  
  - Save and optimize images with resizing and quality options.
- **Robust Uptime Tracking:**  
  - Persist uptime between bot restarts if the disconnect is short.
- **Dynamic Cog Loading & Hot Reloading:**  
  - Automatically loads and reloads extensions as they change.

## Installation

1. **Clone the Repository:**

   ```bash
   git clone https://github.com/ArtiArtem8/stupid-bot-discord.git
   cd stupidbot
   ```

2. **Create and Activate a Virtual Environment:**

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```

3. **Install Dependencies:**

   ```bash
   pip install -r requirements
   ```

4. **Set Up Environment Variables:**

   Create a `.env` file or set environment variables in your system with at least:

   ```bash
   DISCORD_BOT_TOKEN=your_discord_bot_token
   WOLFRAM_APP_ID=your_wolfram_app_id
   ```

## Configuration

All global configuration is stored in [`config.py`](config.py). You can update settings such as:
- Bot prefix and token.
- File paths for birthdays and user answers.
- Logging configuration and formatting.
- Birthday wishes, questions, and answer lists.

## Running the Bot

After installing dependencies and setting up your environment variables, run:

```bash
python main.py
```
or just run the script using the [`runstupidbot.bat`](runstupidbot.bat) batch file (windows only):

The bot will start, load all cogs (including birthday, WolframAlpha, and message processing), and sync its slash commands.

## Contributing

Contributions are welcome! Feel free to open issues or submit pull requests to improve the project. Please ensure your code adheres to the current project structure and style.

## License

This project is licensed under the [MIT License](LICENSE).
