// AnnaPath Client-Side Utilities

// Real-time food inventory search & filter
function searchFood() {
    const input = document.getElementById("search");
    if (!input) return;

    const filter = input.value.toLowerCase().trim();
    const cards = document.getElementsByClassName("food-card");

    for (let i = 0; i < cards.length; i++) {
        const text = cards[i].innerText.toLowerCase();
        if (text.indexOf(filter) > -1) {
            cards[i].style.display = "";
        } else {
            cards[i].style.display = "none";
        }
    }
}