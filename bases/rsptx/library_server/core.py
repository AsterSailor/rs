from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.get("/")
async def root():
   res = await fetch_library_books()
   return {"books": res}
