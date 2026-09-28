insert into businesses (name, owner_name, phone_number, business_type, subscription_plan)
values ('Demo Provision Store', 'Demo Owner', '2348000000000', 'retail', 'pilot')
on conflict (phone_number) do nothing;

insert into users (business_id, phone_number, display_name, role)
select id, '2348000000000', 'Demo Owner', 'owner'
from businesses
where phone_number = '2348000000000'
on conflict (phone_number) do nothing;
