"""All web forms."""
from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import BooleanField, FloatField, IntegerField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, InputRequired, Length, NumberRange, Optional, Regexp
from shop.helpers import PRODUCT_CATEGORY_CHOICES


class RegistrationForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=4, max=25),
        Regexp(r'^[A-Za-z0-9._-]+$', message='Use only letters, numbers, dots, dashes and underscores.')])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=8, message="Use at least 8 characters.")])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Register')


class LoginForm(FlaskForm):
    # Use email or username to login
    login_identifier = StringField('Username or Email', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Remember Me')
    submit = SubmitField('Login')


class RequestResetForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Request Password Reset')


class ResetPasswordForm(FlaskForm):
    password = PasswordField('Password', validators=[DataRequired(), Length(min=8, message="Use at least 8 characters.")])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Reset Password')


class VerifyEmailForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    verification_code = StringField('Verification Code', validators=[DataRequired(), Length(min=6, max=6)])
    submit = SubmitField('Verify Account')


class ReviewForm(FlaskForm):
    rating = StringField('Rating', validators=[DataRequired()]) # Consider using SelectField for 1-5 stars
    comment = TextAreaField('Comment', validators=[Optional(), Length(max=500)])
    submit = SubmitField('Submit Review')


class UpdateCartForm(FlaskForm): # For updating quantity in cart
    quantity = StringField('Quantity', validators=[DataRequired()]) # Should be IntegerField from wtforms.fields.html5
    submit = SubmitField('Update')


class ProductForm(FlaskForm):
    name = StringField('Product Name', validators=[DataRequired(), Length(max=100)])
    price = FloatField('Current Price (Sale or Regular)', validators=[DataRequired(), NumberRange(min=0.01)]) # This is the actual price
    original_price = FloatField('Original Price (Set if Current Price is a Sale Price, otherwise leave blank or same as Current Price)', 
    validators=[Optional(), NumberRange(min=0)])
    description = TextAreaField('Description', validators=[Optional(), Length(max=1000)]) # Changed from StringField with widget
    category = SelectField('Category', choices=PRODUCT_CATEGORY_CHOICES, validators=[DataRequired()])
    stock = IntegerField('Stock Quantity', validators=[InputRequired(message='Enter the stock (0 if sold out).'), NumberRange(min=0)]) # Changed from StringField
    image = FileField('Product Image', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg', 'gif'])])
    submit = SubmitField('Save Product')


class AdminUserForm(FlaskForm): # For Admin Add/Edit User
    username = StringField('Username', validators=[DataRequired(), Length(min=4, max=25),
        Regexp(r'^[A-Za-z0-9._-]+$', message='Use only letters, numbers, dots, dashes and underscores.')])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password (leave blank to keep current)', validators=[Optional(), Length(min=8, message="Use at least 8 characters.")])
    confirm_password = PasswordField('Confirm Password', validators=[EqualTo('password')])
    is_admin = BooleanField('Is Admin?')
    confirmed = BooleanField('Email Confirmed?') # Allow admin to manually confirm
    submit = SubmitField('Save User')


class AdminResetPasswordForm(FlaskForm):
    new_password = PasswordField('New Password', validators=[DataRequired(), Length(min=8, message="Use at least 8 characters.")])
    confirm_password = PasswordField('Confirm New Password', validators=[DataRequired(), EqualTo('new_password')])
    submit = SubmitField('Reset Password')


KENYAN_PHONE_RE = r'^(?:\+?254|0)[17]\d{8}$'


_strip_spaces = lambda v: v.replace(' ', '').replace('-', '') if v else v


class CheckoutForm(FlaskForm):
    contact_name = StringField('Full name', validators=[DataRequired(message='Please enter the name of the person receiving the order.'), Length(max=80)])
    contact_phone = StringField('Phone number', filters=[_strip_spaces], validators=[
        DataRequired(message='We need a phone number to reach you about your order.'),
        Regexp(KENYAN_PHONE_RE, message='Enter a Kenyan number like 0712345678 or +254712345678.')])
    delivery_zone = StringField('Delivery option', validators=[DataRequired(message='Please choose delivery or pickup.')])
    shipping_address = TextAreaField('Delivery address', validators=[Optional(), Length(max=300)])
    landmark = StringField('Landmark / directions', validators=[Optional(), Length(max=200)])
    location = StringField('Location', validators=[Optional(), Length(max=40)])   # "lat,lng" from the phone's GPS (optional)
    payment_method = StringField('Payment Method', validators=[DataRequired(message='Please choose how you will pay.')])
    # For M-Pesa (defaults to the contact phone when left empty)
    phone_number = StringField('M-Pesa Phone Number', filters=[_strip_spaces], validators=[
        Optional(),
        Regexp(r'^(?:\+?254|0)[17]\d{8}$', message='Enter a Safaricom number like 0712345678 or +254712345678.')
    ])
    submit = SubmitField('Place Order')


class ShopSettingsForm(FlaskForm):
    submit = SubmitField('Save settings')


class DeliveryZoneForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=80)])
    kind = SelectField('Type', choices=[('delivery', 'Delivery area'), ('pickup', 'Pickup point')])
    fee = FloatField('Fee (KSh)', default=0, validators=[Optional(), NumberRange(min=0)])
    free_over = FloatField('Free when the order reaches (KSh, optional)', validators=[Optional(), NumberRange(min=0)])
    eta = StringField('Delivery time (e.g. Same day, 1-2 days)', validators=[Optional(), Length(max=60)])
    details = TextAreaField('Areas covered, or pickup address and opening hours', validators=[Optional(), Length(max=300)])
    sort_order = IntegerField('Order in list (0 is first)', default=0, validators=[Optional(), NumberRange(min=0)])
    is_active = BooleanField('Show at checkout', default=True)
    submit = SubmitField('Save')


class PromotionForm(FlaskForm):
    title = StringField('Promotion Title', validators=[DataRequired(), Length(max=100)])
    image = FileField('Promotion Image', 
                      validators=[Optional(), 
                                  FileAllowed(['jpg', 'png', 'jpeg', 'gif'], 'Images only!')])
    target_url = StringField('Link (e.g. /?category=Electronics or /product/12)',
                             validators=[Optional(), Length(max=255),
                                         Regexp(r'^(/(?!/)\S*|https?://\S+)$', message='Use a link that starts with / (a page on this shop) or https://')])
    alt_text = StringField('Image Alt Text (for accessibility)', 
                           validators=[Optional(), Length(max=150)])
    
    original_price = FloatField('Featured Original Price (Optional, for display on banner)', 
                                validators=[Optional(), NumberRange(min=0)])
    promo_price = FloatField('Featured Promotional Price (Optional, for display on banner)', 
                             validators=[Optional(), NumberRange(min=0)])
    discount_text = StringField('Discount Text (e.g., "50% OFF", "Summer Sale")', 
                                validators=[Optional(), Length(max=50)])
    
    is_active = BooleanField('Active (Show on Homepage)', default=True)
    display_order = IntegerField('Display Order (0 is first)', default=0, 
                                 validators=[Optional(), NumberRange(min=0)])
    submit = SubmitField('Save Promotion')
