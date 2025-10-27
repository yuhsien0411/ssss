#!/bin/bash

echo "🚀 Quick installation script for GRVT-Lighter Hedge Mode"

# Check if virtual environment is active
if [[ "$VIRTUAL_ENV" == "" ]]; then
    echo "❌ Virtual environment not active. Please run: source venv/bin/activate"
    exit 1
fi

echo "✅ Virtual environment is active: $VIRTUAL_ENV"

# Install packages one by one with timeout
echo "📦 Installing packages one by one..."

packages=(
    "python-dotenv>=1.0.0"
    "pytz>=2025.2"
    "aiohttp>=3.8.0"
    "websocket-client>=1.6.0"
    "pydantic>=1.8.0"
    "pycryptodome>=3.15.0"
    "ecdsa>=0.17.0"
    "requests==2.32.5"
    "tenacity>=9.1.2"
    "websockets>=12.0"
    "cryptography>=41.0.0"
)

for package in "${packages[@]}"; do
    echo "Installing $package..."
    timeout 60 pip install "$package" || {
        echo "⚠️ Timeout or error installing $package, trying with --break-system-packages"
        pip install "$package" --break-system-packages
    }
done

# Install Lighter SDK separately
echo "🔧 Installing Lighter SDK..."
timeout 120 pip install git+https://github.com/elliottech/lighter-python.git@d0009799970aad54ebb940aa3dc90cbc00028c54 || {
    echo "⚠️ Timeout installing Lighter SDK, trying with --break-system-packages"
    pip install git+https://github.com/elliottech/lighter-python.git@d0009799970aad54ebb940aa3dc90cbc00028c54 --break-system-packages
}

# Install optional packages
echo "📚 Installing optional packages..."
pip install bpx-py --break-system-packages 2>/dev/null || echo "⚠️ Skipping bpx-py (optional)"

echo "✅ Installation complete!"
echo ""
echo "Next steps:"
echo "1. Set up .env file: cp env_example.txt .env && nano .env"
echo "2. Run hedge mode: python hedge_mode.py --exchange grvt --ticker BTC --size 0.001 --iter 10"
