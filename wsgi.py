from app import app, data

# warm cache before first request
data.refresh()

if __name__ == "__main__":
    app.run()
