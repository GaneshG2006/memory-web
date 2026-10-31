# Contributing to Memory Web

Thanks for your interest in contributing!

## Setup

Install the frontend dependencies:

```sh
cd frontend
npm install
```

Install the backend dependencies:

```sh
cd ../backend
pip install -r requirements.txt
```

## Before Submitting

Run the frontend tests and production build:

```sh
cd frontend
npm test
npm run build
```

Run the backend tests:

```sh
cd backend
python -m pytest
```

Keep changes focused and include relevant tests when adding functionality. Never commit API keys, passwords, or environment files.

## Pull Requests

Use a descriptive commit message and explain what changed and why. Include relevant test results in your pull request description.
