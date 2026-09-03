comment on column keeper_contracts.owner_id is
    'The owner who signed the contract. Never updated. A player under contract can
     be traded and the contract terms hold, so the current holder comes from
     keeper_selections.team_id for the season in question, not from here.';
