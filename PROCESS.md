# How to Use This
- Put images into input/
- Run commands according to what kinds of content you're putting into input/
- If the content is very basic and simple shapes that are very common, then run them through in a big batch
- If not, you may need to edit the prompt in enhance.py to give me specifications specific to the unique piece of article
- For things that have very contrasting colors in it's design (black and white, stripes, etc.), you may need to run the Open AI separately, then remove the image background on the mac separately because the AI image background remover will cause issues
`python3 process.py done_input some_output_dir --skip-bg-removal`
