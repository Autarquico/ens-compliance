SELECT domain, name, value, CASE WHEN username IS NOT NULL AND username != '' THEN 1 ELSE 0 END AS user_scoped, manual FROM managed_policies WHERE domain = 'com.apple.SoftwareUpdate';
