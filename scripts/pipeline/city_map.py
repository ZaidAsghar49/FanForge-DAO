
CITY_COUNTRY_MAP = {
    # Australia
    'Perth': 'Australia', 'Hobart': 'Australia', 'Adelaide Oval': 'Australia', 'Brisbane': 'Australia',
    'Melbourne Cricket Ground': 'Australia', 'Sydney Cricket Ground': 'Australia', 'Victoria': 'Australia',
    'Canberra': 'Australia', 'Sydney Showground Stadium': 'Australia', 'Melbourne': 'Australia',
    'Sydney': 'Australia', 'Albury': 'Australia', 'Bendigo': 'Australia', 'Alice Springs': 'Australia',
    'Coffs Harbour': 'Australia', 'Adelaide': 'Australia', 'Geelong': 'Australia', 'Wollongong': 'Australia',
    'Mackay': 'Australia', 'Cairns': 'Australia', 'Launceston': 'Australia', 'Perth Stadium': 'Australia',

    # England / UK / Ireland
    'Belfast': 'Ireland', 'Strabane': 'Ireland', 'Dublin': 'Ireland', 'Waringstown': 'Ireland', 'Comber': 'Ireland',
    'London': 'England', 'Birmingham': 'England', 'Cardiff': 'England', 'Leeds': 'England', 'Southampton': 'England',
    'Taunton': 'England', 'Nottingham': 'England', 'Manchester': 'England', 'Chester-le-Street': 'England',
    'Southend-on-Sea': 'England', 'Chelmsford': 'England', 'Cheltenham': 'England', 'Worcester': 'England',
    'Northampton': 'England', 'Chesterfield': 'England', 'Arundel Castle Cricket Club Ground': 'England',
    'Liverpool': 'England', 'Beckenham': 'England', 'Hove': 'England', 'Uxbridge Cricket Club Ground': 'England',
    'Canterbury': 'England', 'Richmond': 'England', 'Leicester': 'England', 'Derby': 'England', 'Brighton': 'England',
    'Gosforth': 'England', 'Market Warsop': 'England', 'Radlett': 'England', 'Blackpool': 'England',
    'Eastbourne': 'England', 'Swansea': 'England', 'Southport': 'England', 'Guildford': 'England',
    'Scarborough': 'England', 'Uxbridge': 'England', 'Colwyn Bay': 'England', 'Tunbridge Wells': 'England',
    'Northwood': 'England', 'Oakham': 'England', 'Loughborough': 'England', 'York': 'England', 'Arundel': 'England',
    'Bready': 'Ireland', 'Eglinton': 'Ireland', 'Cork': 'Ireland', 'Londonderry': 'Ireland', 'Edinburgh': 'Scotland',
    'Stirling': 'Scotland',

    # South Africa
    'Johannesburg': 'South Africa', 'Durban': 'South Africa', 'Paarl': 'South Africa', 'Benoni': 'South Africa',
    'Bloemfontein': 'South Africa', 'Potchefstroom': 'South Africa', 'Port Elizabeth': 'South Africa',
    'Centurion': 'South Africa', 'Kimberley': 'South Africa', 'Cape Town': 'South Africa', 'East London': 'South Africa',

    # West Indies
    'St Lucia': 'West Indies', 'Guyana': 'West Indies', 'Barbados': 'West Indies', 'Jamaica': 'West Indies',
    'Antigua': 'West Indies', 'Trinidad': 'West Indies', 'Warner Park, Basseterre': 'West Indies',
    'St Kitts': 'West Indies', 'Dominica': 'West Indies', 'Lauderhill': 'USA', # Usually WI host here but geography is USA

    # New Zealand
    'Christchurch': 'New Zealand', 'Nelson': 'New Zealand', 'Napier': 'New Zealand', 'Mount Maunganui': 'New Zealand',
    'Wellington': 'New Zealand', 'Hamilton': 'New Zealand', 'Auckland': 'New Zealand', 'Dunedin': 'New Zealand',
    'Lincoln': 'New Zealand', 'Saxton Oval': 'New Zealand', 'Hagley Oval': 'New Zealand', 'Bay Oval': 'New Zealand',
    'Cello Basin Reserve': 'New Zealand', 'Kennards Hire Community Oval': 'New Zealand', 'Seddon Park': 'New Zealand',
    'University of Otago Oval': 'New Zealand', 'Nelson Park': 'New Zealand', 'Mainpower Oval': 'New Zealand',
    'Colin Maiden Park': 'New Zealand', 'Cobham Oval': 'New Zealand', 'Eden Park': 'New Zealand', 'Westpac Stadium': 'New Zealand',
    'Whangarei': 'New Zealand', 'Rangiora': 'New Zealand', 'Alexandra': 'New Zealand', 'New Plymouth': 'New Zealand',
    'Invercargill': 'New Zealand', 'Molyneux Park': 'New Zealand', 'McLean Park': 'New Zealand',

    # India
    'Kanpur': 'India', 'Kolkata': 'India', 'Indore': 'India', 'Dharmasala': 'India', 'Delhi': 'India',
    'Chandigarh': 'India', 'Ranchi': 'India', 'Visakhapatnam': 'India', 'Rajkot': 'India', 'Mumbai': 'India',
    'Chennai': 'India', 'Pune': 'India', 'Cuttack': 'India', 'Nagpur': 'India', 'Bangalore': 'India',
    'Hyderabad': 'India', 'Guwahati': 'India', 'Thiruvananthapuram': 'India', 'Bengaluru': 'India',
    'Dharamsala': 'India', 'Saurashtra Cricket Association Stadium': 'India',
    'Shaheed Veer Narayan Singh International Stadium': 'India', 'Arun Jaitley Stadium': 'India',
    'Dr. Y.S. Rajasekhara Reddy ACA VDCA Cricket Stadium': 'India', 'JSCA International Stadium Complex': 'India',
    'Dr P.V.G. Raju ACA Sports Complex': 'India', 'Vadodara': 'India', 'JU Second Campus, Salt Lake': 'India',
    'Eden Gardens': 'India',

    # Sri Lanka
    'Colombo': 'Sri Lanka', 'Galle International Stadium': 'Sri Lanka', 'Hambantota': 'Sri Lanka',
    'Pallekele International Cricket Stadium': 'Sri Lanka', 'Rangiri Dambulla International Stadium': 'Sri Lanka',
    'Colombo Cricket Club Ground': 'Sri Lanka',

    # Pakistan
    'Lahore': 'Pakistan', 'Karachi': 'Pakistan',

    # Bangladesh
    'Mirpur': 'Bangladesh', 'Chittagong': 'Bangladesh', 'Dhaka': 'Bangladesh', 'Chattogram': 'Bangladesh',
    'Cox\'s Bazar': 'Bangladesh', 'Sylhet': 'Bangladesh', 'Sylhet International Cricket Stadium': 'Bangladesh',

    # Zimbabwe
    'Harare Sports Club': 'Zimbabwe', 'Bulawayo': 'Zimbabwe', 'Harare': 'Zimbabwe',
    'Bulawayo Athletic Club': 'Zimbabwe', 'Kwekwe': 'Zimbabwe',

    # Others
    'Dubai International Cricket Stadium': 'UAE', 'Abu Dhabi': 'UAE', 'Sharjah Cricket Stadium': 'UAE',
    'Sharjah': 'UAE', 'Dubai': 'UAE',
    'Amstelveen': 'Netherlands', 'Voorburg': 'Netherlands',
    'Nairobi': 'Kenya',
    'Hong Kong': 'Hong Kong',
    'Port Moresby': 'Papua New Guinea',
    'Stockholm': 'Sweden', 'Johor Cricket Academy Oval': 'Malaysia',
    'Los Angeles': 'USA', 'Sano International Cricket Ground': 'Japan',
    'Bangkok': 'Thailand', 'Chiang Mai': 'Thailand', 'Royal Chiangmai Golf Club': 'Thailand',
    'Kampala': 'Uganda', 'Entebbe Cricket Oval': 'Uganda',
    'Windhoek': 'Namibia', 'Kirtipur': 'Nepal'
}
