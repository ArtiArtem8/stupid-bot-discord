# Contributing to StupidBot

Thanks for contributing!

The following is a set of guidelines for contributing to StupidBot. These are mostly guidelines, not rules. Use your best judgment, and feel free to propose changes to this document in a pull request.

## Code of Conduct

This project and everyone participating in it is governed by the Code of Conduct. By participating, you are expected to uphold this code.

## How Can I Contribute?

### Reporting Bugs

This section guides you through submitting a bug report. Following these guidelines helps maintainers and the community understand your report, reproduce the behavior, and find related reports.

- **Use a clear and descriptive title** for the issue to identify the problem.
- **Describe the exact steps which reproduce the problem** in as many details as possible.
- **Provide specific examples** to demonstrate the steps.

### Suggesting Enhancements

This section guides you through submitting an enhancement suggestion, including completely new features and minor improvements to existing functionality.

- **Use a clear and descriptive title** for the issue to identify the suggestion.
- **Provide a step-by-step description of the suggested enhancement** in as many details as possible.
- **Explain why this enhancement would be useful** to most users.

### Pull Requests

The process described here has several goals:

- Maintain StupidBot's quality.
- Fix problems that are important to users.
- Engage the community in working toward the best possible bot.

Please follow these steps to have your contribution considered by the maintainers:

- Follow the style guides.

## Styleguides

### Python Styleguide

All Python code is linted with [Ruff](https://github.com/astral-sh/ruff). Type checking is handled via **Pylance** in VS Code.

- **Linting**: Ensure your code passes `uv run ruff check .`
- **Formatting**: Ensure your code is formatted with `uv run ruff format .`
- **Type Checking**: Ensure no errors are reported by Pylance in VS Code.

### Dependency Management

This project uses `uv` for dependency management.

- **Install Dependencies**: `uv sync`
- **Add Dependency**: `uv add <package>`
- **Remove Dependency**: `uv remove <package>`

### Commit Messages

- Use the present tense ("Add feature" not "Added feature")
- Use the imperative mood ("Move cursor to..." not "Moves cursor to...")
