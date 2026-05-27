# Workshop task

The specific task — what exactly your team adds to the bank — is **announced
by the host out loud** at the start of the workshop. It isn't written here
on purpose.

## The shared frame

Your team is three blocks, one person per block: **retail** (the customer's
mobile bank), **cib** (business logic and decisions) and **backend** (the
data core). The team adds a new feature to the bank for customers. The
feature is done only when **all three blocks** have done their part and
connected:

- retail asks backend for data, and asks cib for the decision;
- cib asks backend for customer data.

Inside the team, agree out loud who exposes which slice of the API. The
seam between blocks is the `CONTRACT.md` file in each block: you write your
endpoints and the request/response shape into it. Neighbours look there —
they don't read each other's code, isolation blocks that. A new endpoint
appeared — update your `CONTRACT.md`, otherwise the neighbour won't know.

## Deviation is fine

This isn't an exam. Nothing stops you from drifting away from the assigned
task and doing whatever is more interesting — experiment. The AI helper
follows you, it does not herd you back into the frame.
