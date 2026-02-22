part_1 = """
<!DOCTYPE html>
<html lang="en">
<head>
   <meta charset="utf-8">
   <meta name="viewport" content="width=device-width, initial-scale=1">
   <title>Complaint Raised – Immediate Action Required</title>
   <link href="https://fonts.googleapis.com/css?family=Montserrat:ital,wght@0,400;0,600;1,400;1,600&display=swap" rel="stylesheet">
   <style>
      .sm-w-full { width: 100% !important; }
      .sm-px-24 { padding-left: 24px !important; padding-right: 24px !important; }
      .sm-py-32 { padding-top: 32px !important; padding-bottom: 32px !important; }
      .sm-leading-32 { line-height: 32px !important; }
      .hover-underline:hover { text-decoration: underline !important; }
      .urgent { color: #d9534f; font-weight: bold; }
   </style>
</head>
<body style="margin: 0; width: 100%; padding: 0; background-color: #eceff1; font-family: 'Montserrat', sans-serif;">
   <div role="article" lang="en" style="background-color: #eceff1; padding: 24px;">
      <table style="width: 100%; max-width: 600px; margin: auto; background-color: #ffffff;">
         <tr>
            <td style="text-align: center; padding: 24px;">
               <a href="https://hajjumrah.co">
                  <img src="https://mycause.com.pk/hajj_logo.png" width="155" alt="Hajj Umrah">
               </a>
            </td>
         </tr>
         <tr>
            <td style="padding: 24px; text-align: left;">
               <p style="font-size: 20px; font-weight: 600;">Hello 
"""

part_2 = """
            ,</p>
               <p class="urgent" style="font-size: 18px; font-weight: 600;">
                  Immediate Attention Required – A Complaint Has Been Raised for Booking Number: 
               
"""

part_3 = """
</p>
               <p style="font-size: 14px;">
                  We are writing to inform you that a complaint has been raised regarding your recent booking. Please review and address this complaint at your earliest convenience. 
               </p>
               <p style="font-size: 16px; font-weight: 600; color: #00936c;">
                  Complaint Title:
                   
            """


part_4 = """

               </p>
               <p style="font-size: 14px;">
                  We kindly request that you prioritize the resolution of this complaint. You can access further details and take action by visiting the following link:
               </p>
               <table cellpadding="0" cellspacing="0">
                  <tr>
                     <td style="border-radius: 4px; background-color: #00936c;">
                        <a href="https://partner.hajjumrah.co/" 
                           style="display: block; padding: 16px 24px; font-size: 16px; font-weight: 600; color: #ffffff; text-decoration: none;">
                           Resolve Complaint &rarr;
                        </a>
                     </td>
                  </tr>
               </table>
"""

part_5 = """
               <p style="font-size: 14px; margin-top: 24px;">
                  Thank you for your prompt attention to this matter.
               </p>
               <p style="font-size: 14px; font-weight: 600;">
                  Sincerely,<br>Hajj Umrah Team
               </p>
            </td>
         </tr>
         <tr>
            <td style="height: 20px;"></td>
         </tr>
      </table>
   </div>
</body>
</html>
"""

# # Combine parts for full HTML
# email_template = part_1 + part_2 + part_3 + part_4 + part_5
