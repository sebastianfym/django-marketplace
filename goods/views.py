import datetime
import decimal
from django.db.models import Q
from django.shortcuts import render, get_object_or_404, redirect
from django.views import View
from django.views.generic import ListView, DetailView, DeleteView
from django.views.generic import DetailView
from app_shop.models import Seller
from cart.models import CartItems
from .forms import DetailProductReviewForm

from .models import Category, Goods, ViewHistory, GoodsInMarket, DetailProductComment, Image, SuperCategory
from django.utils.translation import gettext as _
from .models import Category, Goods, ViewHistory
from django.views.decorators.cache import cache_page
from django.core.cache import cache
from config.settings import CACHES_TIME
from goods.serviсes import CatalogMixin, create_compare, get_all_features, get_different_features
from customers.models import CustomerUser
from discounts.models import Discount


class CategoryView(View):
    """
    Представление для категорий товаров у которых activity = True.
    """

    def get(self, request):
        cache_this = cache_page(3600 * CACHES_TIME)
        categories = SuperCategory.objects.filter(activity=True)
        if categories.update():
            cache.delete(cache_this)
        return render(request, 'category/category.html', context={'categories': categories})


class Catalog(CatalogMixin, ListView):
    model = Goods
    template_name = 'goods/catalog.html'
    paginate_by = 8
    context_object_name = 'catalog'

    def get_queryset(self):
        queryset = self.select_orm_statement()
        return queryset

    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data()
        parameters = self.normalises_values_parameters()
        context.update(parameters)
        context.update({'sellers': Seller.objects.all()})
        context.update({'supercategory': SuperCategory.objects.all()})
        return context


class ShowDetailProduct(DetailView):
    """
    Данный класс служит для детального представления определённого товара.
    """

    model = Goods
    template_name = 'goods/product.html'

    def get_context_data(self, **kwargs):
        context = super(ShowDetailProduct, self).get_context_data(**kwargs)
        product_id = context['goods'].id
        context['seller'] = GoodsInMarket.objects.filter(goods__id=product_id)
        context['review'] = DetailProductComment.objects.filter(goods__id=product_id)
        context['len_review'] = str(len(context['review']))
        context['images'] = Image.objects.filter(product_id=product_id)
        context['image_pict_right'] = context['images'][0]
        context['form'] = DetailProductReviewForm()

        if self.request.user.is_authenticated:
            context['in_cart_or_not'] = CartItems.objects.filter(user=self.request.user,
                                                                 product_in_shop__goods_id=product_id).exists()
        else:
            cart = list()
            if self.request.session.get("cart"):
                cart = self.request.session.get("cart")
            for result, dic_ in enumerate(cart):
                if dic_.get('inplay', '') == 'False':
                    context['in_cart_or_not'] = True
                    break
                else:
                    context['in_cart_or_not'] = False
        return context

    def post(self, request):
        form = DetailProductReviewForm(request.POST)
        if form.is_valid():
            DetailProductComment.objects.create(
                goods=Goods.objects.get(id=self.kwargs['pk']),
                text=form.cleaned_data.get('text'),
                email=form.cleaned_data.get('email'),
                author_name=form.cleaned_data.get('author_name')
            )
            return redirect(f"../{self.kwargs['pk']}/")
        else:
            return redirect(f"../{self.kwargs['pk']}/")


class AddProductToCompareView(View):
    """
    Добавление товара в сравнение
    """

    def get(self, request, id, *args, **kwargs):
        if not request.session.get("compare"):
            request.session["compare"] = list()
        if id not in request.session['compare']:
            request.session['compare'].append(id)
            request.session.modified = True
        else:
            request.session['compare'].remove(id)
            request.session.modified = True
        return redirect(request.META['HTTP_REFERER'])


class DeleteProductFromCompareView(View):
    """
    Удаление товара из списка сравнений
    """

    def get(self, request, id, *args, **kwargs):
        id_product = int(id)
        if id_product in request.session["compare"]:
            request.session["compare"].remove(id_product)
            request.session.modified = True
        return redirect('compare')


class CompareView(View):

    def get(self, request, *args, **kwargs):
        compare_list = request.session.get("compare")
        if compare_list is not None:
            compare_list_products, categories_list, product_features = create_compare(compare_list)
            if len(set(categories_list)) > 1:
                context = {
                    'compare_list': compare_list_products,
                    'different_features': 0,
                    'message': _('It is impossible to compare products from different categories')
                }
                return render(request, 'goods/mycompare.html', context=context)
            all_features = get_all_features(product_features)
            different_features = get_different_features(all_features, compare_list)
            return render(request, 'goods/mycompare.html', {'compare_list': compare_list_products,
                                                            'different_features': different_features})
        else:
            return render(request, 'goods/mycompare.html')


def add_to_view_history(customer, goods: Goods) -> None:
    ViewHistory.objects.update_or_create(customer=customer,
                                         goods=goods,
                                         defaults={'last_view': datetime.datetime.now()})


def remove_from_view_history(customer, goods: Goods) -> None:
    ViewHistory.objects.delete(customer=customer, goods=goods)


def is_in_view_history(customer, goods: Goods) -> bool:
    if ViewHistory.objects.get(customer=customer, goods=goods):
        return True
    else:
        return False


class HistoryList(ListView):
    model = ViewHistory
    template_name = 'goods/historyview.html'
    context_object_name = 'history_list'
    paginate_by = 8

    def get_queryset(self):
        return ViewHistory.objects.filter(customer=self.request.user)[:20]


def price_with_discount(goods: Goods, old_price=0.00) -> decimal:
    """
    Метод для расчета скидки по товару. Если не указана старая цена, выбирается максимальная из всех предложенных.
    Выбираются все действующие скидки на товар, или на его категорию, возвращается цена с максимальной скидкой,
    но не меньше 1 руб.
    :param goods: товар, по которому ищем цену
    :param old_price: старая цена
    :return: цена с учетом скидки
    """
    if not old_price:
        old_price = GoodsInMarket.objects.filter(goods=goods).aggregate(max('price'))
    today = datetime.date.today()
    goods_discounts = Discount.objects.filter(Q(goods_1=goods) | Q(category_1=goods.category),
                                              discount_type__pk=1,
                                              date_start__lte=today,
                                              date_end__gte=today
                                              )
    percent_discount = goods_discounts.objects.filter(discount_mech__pk=1).aggregate(max('discount_value'))
    percent_discount_price = round((1 - percent_discount) / 100 * old_price, 2)
    absolute_discount = goods_discounts.objects.filter(discount_mech__pk=2).aggregate(max('discount_value'))
    absolute_discount_price = old_price - absolute_discount
    if absolute_discount_price <= 0:
        return 1.00
    if percent_discount_price < absolute_discount_price:
        return percent_discount_price
    return absolute_discount_price


def cart_cost(cart: dict) -> dict:
    """
    Метод получает в качестве параметра словарь вида «товар и исходная цена» и пытается применить
    самую приоритетную скидку на корзину или на набор. Если такая скидка есть, то применяется она; если нет,
    то тогда метод получает цену на каждый товар словаря.
    :param cart: {товар: исходная цена}
    :return: {товар: исходная цена, цена со скидкой, применена ли скидка}
    :rtype: dict
    """
    today = datetime.date.today()
    goods_count = len(cart)
    goods_cost = sum(cart.values())
    cart_discount = Discount.objects.filter(Q(discount_type__pk=3),
                                            date_start__lte=today,
                                            date_end__gte=today,
                                            min_amount__lte=goods_count,
                                            max_amount__gte=goods_count,
                                            min_cost__lte=goods_cost,
                                            max_cost__lte=goods_cost,
                                            ).order_by('-weight').first()

    set_discount = Discount.objects.filter(Q(discount_type__pk=2),
                                           Q(goods_1__in=cart.keys()) | Q(category_1__in=cart.keys.category),
                                           Q(goods_2__in=cart.keys()) | Q(category_2__in=cart.keys.category),
                                           date_start__lte=today,
                                           date_end__gte=today,
                                           ).order_by('-weight').first()
    res = {}
    if cart_discount.weight >= set_discount.weight:
        total_discount = cart_discount.value
    elif set_discount:
        total_discount = set_discount.value
    else:
        for goods, price in cart.items():
            new_price = price_with_discount(goods, price)
            if new_price < price:
                res[goods] = (price, new_price, True)
            else:
                res[goods] = (price, price, False)
        else:
            return res
    for goods, price in cart.items():
        res[goods] = (price, round(price * (1 - total_discount / 100)), True)
    return res
