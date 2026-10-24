# Putting the assistant on the salon's own website (for Fiona)

**Not needed for the first release.** The first release is the hosted page at the address Shobana's Netlify site gives it; the owner can simply add a link to that address on the salon's website. This
note is the code to use when she wants the assistant to appear INSIDE a page of the salon's site. The hosted address is not known yet, so `HOSTED-ADDRESS` below must be replaced by it. Until the
page is hosted and the real test has passed, do not put either snippet on a live page.

## Option 1: a link (nothing to install, works everywhere)

```html
<a href="https://HOSTED-ADDRESS/">Ask Aria, our virtual assistant</a>
```

## Option 2: the assistant inside a page (an embedded frame)

```html
<iframe
  src="https://HOSTED-ADDRESS/"
  title="Aria, Glamour Day Spa virtual assistant"
  width="100%" height="720"
  style="border:0; max-width:720px; display:block; margin:0 auto"
  allow="microphone"
  loading="lazy"></iframe>
```

- `allow="microphone"` is what lets the Talk to Aria button ask the visitor's browser for the microphone from inside the frame. Without it, typing still works and voice reports that the microphone could not be used.
- On a Wix page: Add > Embed Code > Embed HTML, paste the snippet. Give the element enough height (about 720 pixels on a computer, taller on a phone).
- The hosted page allows framing by `https://www.warrenglamourdayspanj.com` and `https://warrenglamourdayspanj.com` only. A different address (a staging copy of the salon's site, for example) would be refused until it is added to the page's `_headers` (`frame-ancestors`) by the operator.
- The assistant is information only. It cannot book; it points to the Book Online page and the phone number. Nothing in the snippet gives the page access to the salon's site, its forms or its visitors' data.
- Visitors should see the same notice as on the hosted page (it is inside the frame): Aria is an AI assistant, calls and chats may be recorded and kept by the service provider.
